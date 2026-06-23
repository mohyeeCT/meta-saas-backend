import os
import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

from fastapi import HTTPException
from google.auth.exceptions import RefreshError

from credentials import (
    hydrate_job_settings,
    load_active_gsc_credentials,
    load_user_credentials,
    mark_gsc_reconnect_required,
)
from routers.meta import MetaRow as JobRow, MetaSettings as JobSettings, MetaJobRequest as RunJobRequest
from routers import meta, jobs
from utils import gsc


SERVICE_ACCOUNT = {
    "method": "service_account",
    "service_account": {"client_email": "runtime@example.com", "private_key": "runtime-private-key"},
}
OAUTH = {"method": "google_oauth", "refresh_token_ciphertext": "v1:runtime-ciphertext"}
RECONNECT_ERROR = "Google Search Console reconnect required."
UNAVAILABLE_ERROR = "Selected Google Search Console connection unavailable."
SECRETS = ("runtime-api-secret", "runtime-dfs-secret", "v1:runtime-ciphertext", "runtime-private-key")


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, sb, table):
        self.sb = sb
        self.table = table
        self.filters = []
        self.in_filters = []
        self.operation = "select"
        self.payload = None

    def select(self, _columns):
        return self

    def insert(self, payload):
        self.operation, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.operation, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.in_filters.append((column, tuple(values)))
        return self

    def execute(self):
        self.sb.executed.append(self)
        source = self.sb.tables.get(self.table, [])
        if isinstance(source, Exception):
            raise source
        if self.operation == "insert":
            return _Response([{"id": "job-new", **self.payload}])
        rows = [
            row for row in source
            if all(row.get(key) == value for key, value in self.filters)
            and all(row.get(key) in values for key, values in self.in_filters)
        ]
        if self.operation == "update":
            for row in rows:
                row.update(self.payload)
        return _Response(rows)


class _Supabase:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.executed = []

    def table(self, name):
        return _Query(self, name)


class _FinalWriteQuery(_Query):
    def execute(self):
        if self.operation == "update" and (self.payload or {}).get("status") == "complete":
            self.sb.executed.append(self)
            raise RuntimeError("private-final-write-secret")
        return super().execute()


class _FinalWriteSupabase(_Supabase):
    def table(self, name):
        return _FinalWriteQuery(self, name)


class _BackgroundTasks:
    def __init__(self):
        self.calls = []

    def add_task(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))


class _DatabaseError(Exception):
    def __init__(self, code):
        self.code = code


def _tables(method="service_account", oauth_status="connected"):
    return {
        "user_settings": [{
            "user_id": "user-1",
            "gsc_auth_method": method,
            "provider_settings": {"api_key": "runtime-api-secret", "dfs_password": "runtime-dfs-secret"},
        }],
        "user_credentials": [{
            "user_id": "user-1",
            "provider_settings": {},
            "gsc_service_account": SERVICE_ACCOUNT["service_account"],
        }],
        "gsc_oauth_connections": [{
            "user_id": "user-1",
            "status": oauth_status,
            "refresh_token_ciphertext": OAUTH["refresh_token_ciphertext"],
        }],
        "jobs": [],
    }


def _runtime_settings(envelope=OAUTH):
    return {
        "provider": "Claude",
        "api_key": "runtime-api-secret",
        "dfs_password": "runtime-dfs-secret",
        "use_gsc": True,
        "site_url": "sc-domain:example.com",
        "_gsc_credentials": envelope,
    }


def _stored_job(error=None):
    row = {
        "id": "job-1",
        "user_id": "user-1",
        "settings": {"provider": "Claude", "use_gsc": True, "site_url": "sc-domain:example.com"},
        "rows": [{"url": "https://example.com/page", "keyword": "manual"}],
        "results": [{}],
    }
    if error is not None:
        row["error"] = error
    return row


def _assert_persistence_is_secret_free(test_case, sb):
    payloads = repr([query.payload for query in sb.executed if query.payload is not None])
    for secret in SECRETS:
        test_case.assertNotIn(secret, payloads)
    test_case.assertNotIn("_gsc_credentials", payloads)
    test_case.assertNotIn("_gsc_service_account", payloads)


class CredentialSelectionTests(unittest.TestCase):
    def test_get_and_duplicate_strip_legacy_secrets_without_mutating_source(self):
        legacy_settings = {
            "provider": "Claude", "api_key": "legacy-api", "dfs_password": "legacy-dfs",
            "jina_api_key": "legacy-jina", "gsc_service_account": {"private_key": "legacy-gsc"},
            "_gsc_credentials": {"refresh_token_ciphertext": "legacy-oauth"},
            "_gsc_service_account": {"private_key": "legacy-runtime-gsc"},
        }
        source = {**_stored_job(), "name": "Legacy", "settings": legacy_settings}
        sb = _Supabase({"jobs": [source]})
        with patch.object(jobs, "get_supabase", return_value=sb):
            response = jobs.get_job("job-1", user=SimpleNamespace(id="user-1"))
        with patch.object(jobs, "enforce_rate_limit"):
            jobs.duplicate_job("job-1", user=SimpleNamespace(id="user-1"), sb=sb)
        self.assertEqual(response["settings"], {"provider": "Claude"})
        insert = [query for query in sb.executed if query.operation == "insert"][-1]
        self.assertEqual(insert.payload["settings"], {"provider": "Claude"})
        self.assertEqual(source["settings"], legacy_settings)

    def test_selector_supports_both_authoritative_modes(self):
        for method, expected in (("service_account", SERVICE_ACCOUNT), ("google_oauth", OAUTH)):
            with self.subTest(method=method):
                self.assertEqual(load_active_gsc_credentials(_Supabase(_tables(method)), "user-1"), expected)

    def test_selector_missing_invalid_and_inactive_never_falls_back(self):
        cases = [
            ("google_oauth", "reconnect_required"),
            ("invalid_method", "connected"),
        ]
        for method, status in cases:
            with self.subTest(method=method, status=status):
                self.assertIsNone(load_active_gsc_credentials(_Supabase(_tables(method, status)), "user-1"))

        tables = _tables("service_account")
        tables["user_credentials"][0]["gsc_service_account"] = None
        self.assertIsNone(load_active_gsc_credentials(_Supabase(tables), "user-1"))

    def test_only_recognized_server_credential_migration_errors_are_ignored(self):
        for code in ("PGRST204", "PGRST205", "42P01", "42703"):
            tables = _tables()
            tables["user_credentials"] = _DatabaseError(code)
            self.assertEqual(load_user_credentials(_Supabase(tables), "user-1")["provider_settings"]["api_key"], "runtime-api-secret")

        tables = _tables()
        tables["user_credentials"] = _DatabaseError("50000")
        with self.assertRaises(_DatabaseError):
            load_user_credentials(_Supabase(tables), "user-1")

    def test_hydration_strips_all_incoming_secrets_then_uses_server_selection(self):
        incoming = {
            "provider": "Claude",
            "api_key": "attacker-api",
            "dfs_password": "attacker-dfs",
            "jina_api_key": "attacker-jina",
            "_gsc_service_account": {"private_key": "attacker-key"},
            "_gsc_credentials": {"method": "google_oauth", "refresh_token_ciphertext": "attacker-token"},
        }
        hydrated = hydrate_job_settings(_Supabase(_tables("service_account")), "user-1", incoming)
        self.assertEqual(hydrated["_gsc_credentials"], SERVICE_ACCOUNT)
        self.assertEqual(hydrated["api_key"], "runtime-api-secret")
        self.assertNotIn("_gsc_service_account", hydrated)
        self.assertNotIn("attacker", repr(hydrated))

    def test_reconnect_marker_is_tenant_status_and_ciphertext_stale_safe(self):
        tables = _tables("google_oauth")
        sb = _Supabase(tables)
        self.assertTrue(mark_gsc_reconnect_required(sb, "user-1", OAUTH["refresh_token_ciphertext"]))
        query = sb.executed[-1]
        self.assertEqual(query.filters, [
            ("user_id", "user-1"),
            ("status", "connected"),
            ("refresh_token_ciphertext", OAUTH["refresh_token_ciphertext"]),
        ])
        self.assertEqual(query.payload["last_error_code"], "refresh_failed")

        for user_id, ciphertext in (("other-user", OAUTH["refresh_token_ciphertext"]), ("user-1", "v1:stale")):
            self.assertFalse(mark_gsc_reconnect_required(_Supabase(_tables("google_oauth")), user_id, ciphertext))


class GscClientTests(unittest.TestCase):
    def test_scope_and_service_account_alias_are_exact(self):
        self.assertEqual(gsc.GSC_SCOPES, ["https://www.googleapis.com/auth/webmasters.readonly"])
        with patch.object(gsc, "ServiceAccountCredentials", create=True) as credentials, patch.object(gsc, "build") as build:
            gsc.get_gsc_client(SERVICE_ACCOUNT)
        credentials.from_service_account_info.assert_called_once_with(
            SERVICE_ACCOUNT["service_account"], scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
        )
        build.assert_called_once_with("searchconsole", "v1", credentials=credentials.from_service_account_info.return_value)

    def test_oauth_reads_env_before_decrypt_then_refreshes_before_build(self):
        order = Mock()
        credentials = Mock()
        request = Mock()
        with (
            patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_ID": "client-id", "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret"}, clear=True),
            patch.object(gsc, "decrypt_secret", return_value="refresh-token", create=True) as decrypt,
            patch.object(gsc, "OAuthCredentials", return_value=credentials, create=True) as oauth_credentials,
            patch.object(gsc, "Request", return_value=request, create=True),
            patch.object(gsc, "build") as build,
        ):
            order.attach_mock(decrypt, "decrypt")
            order.attach_mock(oauth_credentials, "credentials")
            order.attach_mock(credentials.refresh, "refresh")
            order.attach_mock(build, "build")
            gsc.get_gsc_client({**OAUTH, "client_id": "ignored", "client_secret": "ignored"})
        self.assertEqual(order.mock_calls, [
            call.decrypt(OAUTH["refresh_token_ciphertext"]),
            call.credentials(token=None, refresh_token="refresh-token", token_uri=gsc.TOKEN_URI, client_id="client-id", client_secret="client-secret", scopes=gsc.GSC_SCOPES),
            call.refresh(request),
            call.build("searchconsole", "v1", credentials=credentials),
        ])

    def test_oauth_sanitizes_env_values_before_building_credentials(self):
        credentials = Mock()
        with (
            patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_ID": "\ufeffclient-id\n", "GOOGLE_OAUTH_CLIENT_SECRET": " client-secret\t"}, clear=True),
            patch.object(gsc, "decrypt_secret", return_value="refresh-token", create=True),
            patch.object(gsc, "OAuthCredentials", return_value=credentials, create=True) as oauth_credentials,
            patch.object(gsc, "Request", create=True),
            patch.object(gsc, "build"),
        ):
            gsc.get_gsc_client(OAUTH)

        self.assertEqual(oauth_credentials.call_args.kwargs["client_id"], "client-id")
        self.assertEqual(oauth_credentials.call_args.kwargs["client_secret"], "client-secret")

    def test_missing_env_precedes_decrypt_and_invalid_envelopes_are_safe(self):
        with patch.dict(os.environ, {"GOOGLE_OAUTH_CLIENT_SECRET": "secret"}, clear=True), patch.object(gsc, "decrypt_secret", create=True) as decrypt:
            with self.assertRaisesRegex(gsc.GscOAuthConfigError, "Google OAuth configuration is incomplete"):
                gsc.get_gsc_client(OAUTH)
            decrypt.assert_not_called()

        for envelope in (None, "private", {}, {"method": "service_account"}, {"method": "google_oauth"}):
            with self.subTest(envelope=envelope), self.assertRaisesRegex(ValueError, "^Invalid GSC credentials$"):
                gsc.get_gsc_client(envelope)


class RuntimePathTests(unittest.TestCase):
    def test_persistence_capable_background_helpers_require_user_id(self):
        for function in (
            meta._is_cancelled,
            meta._update_job,
            meta._process_single_row,
            meta._process_job,
            jobs._persist_gsc_error,
            jobs._clear_gsc_runtime_error,
            jobs._clear_credentials_runtime_error,
            jobs._get_runtime_gsc_client,
            jobs._rerun_single_row,
            jobs._rerun_multiple_rows,
        ):
            with self.subTest(function=function.__name__):
                parameter = inspect.signature(function).parameters["user_id"]
                self.assertIs(parameter.default, inspect.Parameter.empty)

    def test_initial_hydration_failure_returns_exact_503_without_insert_or_background(self):
        sentinel = "private-hydration-secret"
        sb = _Supabase(_tables("google_oauth"))
        background = _BackgroundTasks()
        request = RunJobRequest(rows=[JobRow(url="https://example.com/page")], settings=JobSettings())

        with (
            patch.object(meta, "enforce_job_start"),
            patch.object(meta, "enforce_rate_limit"),
            patch.object(meta, "hydrate_job_settings", side_effect=RuntimeError(sentinel)),
        ):
            with self.assertRaises(HTTPException) as raised:
                meta.run_meta_job(request, background, user=SimpleNamespace(id="user-1"), sb=sb)

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            raised.exception.detail,
            "Saved credentials are temporarily unavailable. Please try again.",
        )
        self.assertIsNone(raised.exception.__cause__)
        self.assertFalse([query for query in sb.executed if query.table == "jobs"])
        self.assertEqual(background.calls, [])
        self.assertNotIn(sentinel, repr(raised.exception))

    def test_background_job_queries_are_tenant_scoped_and_wrong_tenant_is_untouched(self):
        own_job = _stored_job()
        wrong_job = {**_stored_job(), "user_id": "other-user", "results": [{"tenant": "other"}]}
        sb = _Supabase({"jobs": [own_job, wrong_job]})
        settings = {**_runtime_settings(), "use_gsc": False}

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=settings),
            patch.object(meta, "_process_single_row", return_value={"status": "ok"}),
        ):
            jobs._rerun_single_row(
                "job-1", 0, own_job["rows"], own_job["settings"], sb, user_id="user-1"
            )

        job_queries = [query for query in sb.executed if query.table == "jobs"]
        self.assertTrue(job_queries)
        for query in job_queries:
            self.assertIn(("id", "job-1"), query.filters)
            self.assertIn(("user_id", "user-1"), query.filters)
        self.assertEqual(wrong_job["results"], [{"tenant": "other"}])

    def test_hydration_failure_update_failure_is_guarded(self):
        sb = _Supabase({"jobs": RuntimeError("private-database-secret")})
        with patch.object(
            jobs,
            "hydrate_job_settings",
            side_effect=RuntimeError("private-hydration-secret"),
        ):
            jobs._rerun_single_row(
                "job-1",
                0,
                _stored_job()["rows"],
                _stored_job()["settings"],
                sb,
                user_id="user-1",
            )

    def test_single_rerun_generic_failure_update_failure_is_guarded(self):
        sb = _Supabase({"jobs": RuntimeError("private-database-secret")})
        settings = {**_runtime_settings(), "use_gsc": False}

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=settings),
            patch.object(meta, "_process_single_row", side_effect=RuntimeError("private-provider-secret")),
        ):
            jobs._rerun_single_row(
                "job-1",
                0,
                _stored_job()["rows"],
                _stored_job()["settings"],
                sb,
                user_id="user-1",
            )

    def test_processing_failures_persist_only_fixed_secret_free_messages(self):
        sentinel = "provider-secret-sentinel"
        sb = _Supabase({"jobs": [_stored_job()]})
        settings = {**_runtime_settings(), "use_gsc": False}

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=settings),
            patch.object(meta, "_process_single_row", side_effect=RuntimeError(sentinel)),
        ):
            jobs._rerun_multiple_rows(
                "job-1", [0], _stored_job()["rows"], _stored_job()["settings"], sb, "user-1"
            )

        payloads = repr([query.payload for query in sb.executed if query.payload is not None])
        self.assertNotIn(sentinel, payloads)
        self.assertIn("Row processing failed.", payloads)

    def test_bulk_rerun_results_read_failure_is_guarded_and_secret_free(self):
        sentinel = "private-results-read-secret"
        sb = _Supabase({"jobs": RuntimeError(sentinel)})
        settings = {**_runtime_settings(), "use_gsc": False}

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=settings),
            patch.object(meta, "_process_single_row") as process,
        ):
            jobs._rerun_multiple_rows(
                "job-1", [0], _stored_job()["rows"], _stored_job()["settings"], sb, "user-1"
            )

        process.assert_not_called()
        payloads = [query.payload for query in sb.executed if query.payload is not None]
        self.assertIn({
            "status": "failed",
            "error": "Re-run results could not be saved. Please try again.",
            "current_step": "Re-run failed: results could not be saved.",
            "updated_at": "now()",
        }, payloads)
        self.assertNotIn(sentinel, repr(payloads))

    def test_bulk_rerun_final_write_failure_persists_fixed_safe_state(self):
        sb = _FinalWriteSupabase({"jobs": [_stored_job()]})
        settings = {**_runtime_settings(), "use_gsc": False}

        with (
            patch.object(jobs, "hydrate_job_settings", return_value=settings),
            patch.object(meta, "_process_single_row", return_value={"status": "ok"}),
        ):
            jobs._rerun_multiple_rows(
                "job-1", [0], _stored_job()["rows"], _stored_job()["settings"], sb, "user-1"
            )

        self.assertEqual(sb.tables["jobs"][0]["status"], "failed")
        self.assertEqual(
            sb.tables["jobs"][0]["error"],
            "Re-run results could not be saved. Please try again.",
        )
        payloads = repr([query.payload for query in sb.executed if query.payload is not None])
        self.assertNotIn("private-final-write-secret", payloads)

    def test_successful_single_and_bulk_retry_clear_only_credential_error(self):
        cases = (
            (jobs._rerun_single_row, None),
            (jobs._rerun_multiple_rows, [0]),
        )
        for function, indices in cases:
            for existing_error, expected_error in (
                ("Saved credentials are temporarily unavailable.", None),
                ("Unrelated job failure", "Unrelated job failure"),
                (RECONNECT_ERROR, RECONNECT_ERROR),
            ):
                with self.subTest(function=function.__name__, existing_error=existing_error):
                    sb = _Supabase({"jobs": [_stored_job(existing_error)]})
                    settings = {**_runtime_settings(), "use_gsc": False}
                    with (
                        patch.object(jobs, "hydrate_job_settings", return_value=settings),
                        patch.object(meta, "_process_single_row", return_value={"status": "ok"}),
                        patch.object(meta, "_update_job"),
                    ):
                        if indices is None:
                            function(
                                "job-1",
                                0,
                                _stored_job()["rows"],
                                _stored_job()["settings"],
                                sb,
                                user_id="user-1",
                            )
                        else:
                            function(
                                "job-1",
                                indices,
                                _stored_job()["rows"],
                                _stored_job()["settings"],
                                sb,
                                "user-1",
                            )

                    self.assertEqual(sb.tables["jobs"][0].get("error"), expected_error)
                    clear_queries = [
                        query for query in sb.executed
                        if query.operation == "update" and query.payload == {"error": None}
                    ]
                    self.assertEqual(len(clear_queries), 1)
                    self.assertEqual(clear_queries[0].filters, [
                        ("id", "job-1"),
                        ("user_id", "user-1"),
                    ])
                    self.assertEqual(clear_queries[0].in_filters, [(
                        "error",
                        ("Saved credentials are temporarily unavailable.",),
                    )])

    def test_single_rerun_hydration_failure_persists_safe_tenant_scoped_failure(self):
        private_detail = "database-password-private-detail"
        sb = _Supabase({"jobs": [{**_stored_job(), "status": "complete"}]})

        with (
            patch.object(jobs, "hydrate_job_settings", side_effect=RuntimeError(private_detail)),
            patch.object(meta, "_process_single_row") as process,
        ):
            jobs._rerun_single_row(
                "job-1",
                0,
                _stored_job()["rows"],
                _stored_job()["settings"],
                sb,
                user_id="user-1",
            )

        process.assert_not_called()
        update = [query for query in sb.executed if query.operation == "update"][-1]
        self.assertEqual(update.filters, [("id", "job-1"), ("user_id", "user-1")])
        self.assertEqual(update.payload, {
            "error": "Saved credentials are temporarily unavailable.",
            "current_step": "Row 1 re-run failed: saved credentials are temporarily unavailable.",
            "updated_at": "now()",
        })
        self.assertNotIn("rerunning", update.payload["current_step"].lower())
        self.assertNotIn(private_detail, repr(update.payload))

    def test_bulk_rerun_hydration_failure_sets_terminal_safe_tenant_scoped_failure(self):
        private_detail = "database-token-private-detail"
        sb = _Supabase({"jobs": [{**_stored_job(), "status": "running"}]})

        with (
            patch.object(jobs, "hydrate_job_settings", side_effect=RuntimeError(private_detail)),
            patch.object(meta, "_process_single_row") as process,
        ):
            jobs._rerun_multiple_rows(
                "job-1",
                [0],
                _stored_job()["rows"],
                _stored_job()["settings"],
                sb,
                "user-1",
            )

        process.assert_not_called()
        update = [query for query in sb.executed if query.operation == "update"][-1]
        self.assertEqual(update.filters, [("id", "job-1"), ("user_id", "user-1")])
        self.assertEqual(update.payload, {
            "status": "failed",
            "error": "Saved credentials are temporarily unavailable.",
            "current_step": "Re-run failed: saved credentials are temporarily unavailable.",
            "updated_at": "now()",
        })
        self.assertNotIn(private_detail, repr(update.payload))

    def test_initial_path_uses_exact_envelope_and_never_persists_secrets(self):
        sb = _Supabase(_tables("google_oauth"))
        background = _BackgroundTasks()
        request = RunJobRequest(name="Runtime", rows=[JobRow(url="https://example.com/page")], settings=JobSettings(use_gsc=True))
        with (
            patch.object(meta, "get_supabase", return_value=sb),
            patch.object(meta, "enforce_job_start"),
            patch.object(meta, "enforce_rate_limit"),
            patch.object(meta, "execute_active_job_write", side_effect=lambda write, _tool: write()),
            patch.object(meta, "hydrate_job_settings", return_value=_runtime_settings()),
        ):
            meta.run_meta_job(request, background, user=SimpleNamespace(id="user-1"), sb=sb)
        function, args, kwargs = background.calls[0]
        self.assertIs(function, meta._process_job)
        self.assertEqual(args, ())
        self.assertEqual(kwargs["gsc_credentials"], OAUTH)
        self.assertEqual(kwargs["user_id"], "user-1")
        self.assertNotIn("sa_info", kwargs)
        _assert_persistence_is_secret_free(self, sb)

    def test_initial_processing_supports_both_modes_and_fixed_errors(self):
        for envelope in (SERVICE_ACCOUNT, OAUTH):
            with (
                self.subTest(method=envelope["method"]),
                patch.object(meta, "get_supabase", return_value=Mock()),
                patch.object(meta, "get_gsc_client", return_value="client") as get_client,
                patch.object(meta, "_process_single_row", return_value={"status": "ok"}) as process,
                patch.object(meta, "_update_job"),
                patch.object(meta, "_is_cancelled", return_value=False),
            ):
                meta._process_job("job-1", [{"url": "https://example.com/page"}], _runtime_settings(envelope), envelope, user_id="user-1")
                get_client.assert_called_once_with(envelope)
                self.assertEqual(process.call_args.kwargs["gsc_client"], "client")
                self.assertEqual(process.call_args.kwargs["gsc_auth_method"], envelope["method"])

        for failure, expected in ((RefreshError("provider detail"), RECONNECT_ERROR), (RuntimeError("provider detail"), UNAVAILABLE_ERROR)):
            updates = []
            with (
                patch.object(meta, "get_supabase", return_value=Mock()),
                patch.object(meta, "get_gsc_client", side_effect=failure),
                patch.object(meta, "mark_gsc_reconnect_required") as mark,
                patch.object(meta, "_process_single_row", return_value={"status": "ok"}),
                patch.object(meta, "_update_job", side_effect=lambda _sb, _job, _user, data: updates.append(data)),
                patch.object(meta, "_is_cancelled", return_value=False),
            ):
                meta._process_job("job-1", [{"url": "https://example.com/page"}], _runtime_settings(), OAUTH, user_id="user-1")
            self.assertIn({"error": expected}, updates)
            if isinstance(failure, RefreshError):
                mark.assert_called_once_with(ANY, "user-1", OAUTH["refresh_token_ciphertext"])

    def test_rerun_client_handles_missing_refresh_generic_and_exact_error_clear(self):
        cases = [
            ({"use_gsc": True}, None, UNAVAILABLE_ERROR),
            (_runtime_settings(), RefreshError("provider detail"), RECONNECT_ERROR),
            (_runtime_settings(), RuntimeError("provider detail"), UNAVAILABLE_ERROR),
        ]
        for settings, failure, expected in cases:
            with self.subTest(expected=expected):
                sb = _Supabase({"jobs": [_stored_job()]})
                with patch("utils.gsc.get_gsc_client", side_effect=failure), patch.object(jobs, "mark_gsc_reconnect_required") as mark:
                    result = jobs._get_runtime_gsc_client(settings, sb, "user-1", "job-1")
                self.assertIsNone(result)
                self.assertEqual(sb.tables["jobs"][0]["error"], expected)
                if isinstance(failure, RefreshError):
                    mark.assert_called_once_with(sb, "user-1", OAUTH["refresh_token_ciphertext"])

        for old_error, expected in ((RECONNECT_ERROR, None), (UNAVAILABLE_ERROR, None), ("Unrelated failure", "Unrelated failure")):
            sb = _Supabase({"jobs": [_stored_job(old_error)]})
            with patch("utils.gsc.get_gsc_client", return_value="client"):
                self.assertEqual(jobs._get_runtime_gsc_client(_runtime_settings(), sb, "user-1", "job-1"), "client")
            self.assertEqual(sb.tables["jobs"][0]["error"], expected)
            clear = [q for q in sb.executed if q.operation == "update" and q.payload == {"error": None}][0]
            self.assertEqual(clear.filters, [("id", "job-1"), ("user_id", "user-1")])
            self.assertEqual(clear.in_filters, [("error", (UNAVAILABLE_ERROR, RECONNECT_ERROR, jobs._GSC_CONFIG_ERROR))])

    def test_single_and_multi_reruns_freshly_hydrate_and_use_exact_envelope(self):
        for function, indices in ((jobs._rerun_single_row, None), (jobs._rerun_multiple_rows, [0])):
            for envelope in (SERVICE_ACCOUNT, OAUTH):
                with self.subTest(function=function.__name__, method=envelope["method"]):
                    sb = _Supabase({"jobs": [_stored_job()]})
                    with (
                        patch.object(jobs, "hydrate_job_settings", return_value=_runtime_settings(envelope)) as hydrate,
                        patch.object(jobs, "_get_runtime_gsc_client", return_value="client") as client,
                        patch.object(meta, "_process_single_row", return_value={"status": "ok"}) as process,
                        patch.object(meta, "_update_job"),
                    ):
                        if indices is None:
                            function("job-1", 0, _stored_job()["rows"], _stored_job()["settings"], sb, user_id="user-1")
                        else:
                            function("job-1", indices, _stored_job()["rows"], _stored_job()["settings"], sb, "user-1")
                    hydrate.assert_called_once_with(sb, "user-1", _stored_job()["settings"])
                    client.assert_called_once_with(_runtime_settings(envelope), sb, "user-1", "job-1")
                    self.assertEqual(process.call_args.kwargs["gsc_client"], "client")
                    self.assertEqual(process.call_args.kwargs["gsc_auth_method"], envelope["method"])
                    _assert_persistence_is_secret_free(self, sb)

    def test_single_row_result_includes_safe_gsc_auth_method_label(self):
        sb = _Supabase({"jobs": [_stored_job()]})
        settings = {
            **_runtime_settings(),
            "dfs_login": "login",
            "dfs_password": "runtime-dfs-secret",
        }
        with (
            patch.object(meta, "get_keyword_overview", return_value={}),
            patch.object(meta, "get_keyword_difficulty", return_value={}),
            patch.object(meta, "generate_copy", return_value={
                "title": "Generated title",
                "description": "Generated description",
                "h1_optimised": "Generated H1",
                "review_notes": "",
            }),
        ):
            result = meta._process_single_row(
                row={"url": "https://example.com/page", "keyword": "manual"},
                settings=settings,
                gsc_client=None,
                gsc_auth_method="google_oauth",
                branded_terms=[],
                used_keywords=set(),
                sb=sb,
                job_id="job-1",
                user_id="user-1",
                row_num=1,
                total_rows=1,
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["gsc_auth_method"], "google_oauth")
        self.assertNotIn("v1:runtime-ciphertext", repr(result))


if __name__ == "__main__":
    unittest.main()
