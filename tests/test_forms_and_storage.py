"""Tests for HTML form parsing, secret storage and the account store."""

from __future__ import annotations

import pytest

from coursectl.errors import Unauthorized
from coursectl.htmlforms import (
    looks_like_credential_form,
    parse_forms,
    pick_progression_form,
)
from coursectl.security import (
    SecretBox,
    generate_api_key,
    hash_api_key,
)
from coursectl.store import Store

LOGIN_HANDLER = """
<form name="form1" action="/idp/profile/SAML2/Redirect/SSO?execution=e1s1" method="post">
  <input type="hidden" name="csrf_token" value="csrf-value-123" />
  <input name="shib_idp_ls_supported" type="hidden" />
  <input name="shib_idp_ls_success.x" type="hidden" value="false" />
  <input type="submit" value="Continue" />
</form>
"""

CREDENTIAL_FORM = """
<form name="loginform" method="POST" action="/ppylogin/ppylogin">
  <input type="text" id="mli" name="mli" />
  <input type="password" id="password" name="password" />
  <input type="submit" name="dologin" value="Login" />
  <input type="checkbox" name="passchangewanted" value="on" />
  <input type="hidden" name="__pybpp__" value="token123" />
  <select name="realm"><option value="a">A</option><option value="b" selected>B</option></select>
</form>
"""


def test_parse_forms_resolves_relative_actions():
    forms = parse_forms(LOGIN_HANDLER, base_url="https://shib.test/idp/profile/SAML2/Redirect/SSO?execution=e1s1")
    assert len(forms) == 1
    assert forms[0].action == "https://shib.test/idp/profile/SAML2/Redirect/SSO?execution=e1s1"


def test_pick_progression_form_finds_login_handler():
    forms = parse_forms(LOGIN_HANDLER, base_url="https://shib.test/x")
    form = pick_progression_form(forms)
    assert form is not None
    assert form.payload()["csrf_token"] == "csrf-value-123"
    assert form.hidden_count == 3


def test_credential_form_detected_and_typed():
    forms = parse_forms(CREDENTIAL_FORM, base_url="https://passportyork.test/ppylogin/ppylogin")
    form = looks_like_credential_form(forms)
    assert form is not None
    assert form.has("mli")
    # Checkboxes are excluded by default so we never silently tick
    # "change my Passport York password".
    assert "passchangewanted" not in form.payload()

    # The real credential payload adds the username/password and echoes the
    # submit button, which is what Passport York expects.
    from coursectl.auth import _build_credential_payload

    built = _build_credential_payload(form, "abc123", "hunter2")
    assert built["mli"] == "abc123"
    assert built["password"] == "hunter2"
    assert built["dologin"] == "Login"
    assert built["__pybpp__"] == "token123"
    assert "passchangewanted" not in built


def test_select_default_follows_selected_option():
    forms = parse_forms(CREDENTIAL_FORM, base_url="https://x.test/")
    select = forms[0].find("realm")
    assert select is not None
    assert select.value == "b"


def test_malformed_html_does_not_raise():
    assert parse_forms("<form><input name='a'<form", base_url="https://x.test/") is not None


def test_credential_payload_never_contains_mutating_fields():
    """Even if the checkbox were mis-typed as hidden, the guard must strip it."""
    from coursectl.auth import NEVER_SUBMIT, _build_credential_payload

    html = """
    <form name="loginform" method="POST" action="/ppylogin/ppylogin">
      <input type="text" name="mli" />
      <input type="password" name="password" />
      <input type="hidden" name="passchangewanted" value="on" />
      <input type="hidden" name="ChangePassword" value="1" />
      <input type="hidden" name="__pybpp__" value="token123" />
    </form>
    """
    form = looks_like_credential_form(parse_forms(html, base_url="https://passportyork.yorku.ca/ppylogin/ppylogin"))
    assert form is not None
    payload = _build_credential_payload(form, "abc123", "hunter2")

    assert "passchangewanted" not in payload
    assert "ChangePassword" not in payload
    assert not {n.lower() for n in payload} & NEVER_SUBMIT
    assert payload["mli"] == "abc123"
    assert payload["__pybpp__"] == "token123"


def test_signin_is_read_only_by_default():
    """The default form payload must never include a checkbox."""
    html = """
    <form action="/x">
      <input type="text" name="a" value="1" />
      <input type="checkbox" name="b" value="on" />
    </form>
    """
    form = parse_forms(html, base_url="https://x.test/")[0]
    assert form.payload() == {"a": "1"}


def test_forced_password_change_is_distinguished_from_bad_login():
    """A page asking for a new password must not be reported as invalid credentials."""
    from coursectl.auth import _looks_like_password_change

    change_html = """
    <html><body><p>Your password has expired and must be changed.</p>
      <form action="/change">
        <input type="password" name="newpassword1" />
        <input type="password" name="newpassword2" />
      </form></body></html>
    """
    form = looks_like_credential_form(parse_forms(change_html, base_url="https://passportyork.yorku.ca/x"))
    assert form is not None
    assert _looks_like_password_change(form, change_html) is True

    # An ordinary rejection page has one password field and no change wording.
    rejection = """
    <html><body><div class="alert alert-danger">Login Incorrect:</div>
      <form action="/ppylogin"><input type="password" name="password" /></form></body></html>
    """
    form2 = looks_like_credential_form(parse_forms(rejection, base_url="https://passportyork.yorku.ca/x"))
    assert form2 is not None
    assert _looks_like_password_change(form2, rejection) is False


# ---------------------------------------------------------------------------
# security
# ---------------------------------------------------------------------------
def test_api_key_shape_and_hashing():
    key, digest, prefix = generate_api_key()
    assert key.startswith("ck_")
    assert len(key) > 30
    assert digest == hash_api_key(key)
    assert prefix.startswith("ck_")
    assert key.startswith(prefix.rstrip("…"))


def test_keys_are_unique():
    keys = {generate_api_key()[0] for _ in range(50)}
    assert len(keys) == 50


def test_secret_box_round_trip_and_tamper(tmp_path):
    path = tmp_path / "fernet.key"
    box = SecretBox.load_or_create(path)
    ciphertext = box.encrypt("moodle-token-material")
    assert ciphertext and ciphertext != "moodle-token-material"
    assert box.decrypt(ciphertext) == "moodle-token-material"

    # A different key must not be able to read it.
    other = SecretBox.load_or_create(tmp_path / "other.key")
    with pytest.raises(Unauthorized):
        other.decrypt(ciphertext)

    # Reloading the same key file keeps working.
    assert SecretBox.load_or_create(path).decrypt(ciphertext) == "moodle-token-material"


def test_secret_box_none_passthrough(tmp_path):
    box = SecretBox.load_or_create(tmp_path / "k")
    assert box.encrypt(None) is None
    assert box.decrypt(None) is None


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------
def _upsert(store: Store, username: str = "abc123", key_hash: str = "h1") -> str:
    account = store.upsert_account(
        username=username,
        site_url="https://moodle.test",
        token_enc="enc-token",
        private_token_enc="enc-priv",
        api_key_hash=key_hash,
        api_key_prefix="ck_abcdefg…",
        display_name="Doe, Jane",
        email="abc123@yorku.ca",
        moodle_user_id=42,
    )
    return account.id


def test_upsert_creates_then_rotates(tmp_path):
    store = Store(tmp_path / "t.db")
    first = _upsert(store, key_hash="h1")
    assert store.get_account_by_username("abc123") is not None
    assert store.get_account_by_api_key("h1") is not None

    second = _upsert(store, key_hash="h2")
    assert first == second, "re-login must reuse the account row"
    assert store.get_account_by_api_key("h1") is None, "old key must stop working"
    assert store.get_account_by_api_key("h2") is not None
    assert len(store.list_accounts()) == 1


def test_revoke_wipes_token_and_key(tmp_path):
    store = Store(tmp_path / "t.db")
    account_id = _upsert(store)
    assert store.revoke_account(account_id) is True

    account = store.get_account_by_id(account_id)
    assert account is not None
    assert account.is_active is False
    assert account.token_enc == ""
    assert account.private_token_enc is None
    # Revoking twice is a no-op.
    assert store.revoke_account(account_id) is False


def test_events_are_recorded(tmp_path):
    store = Store(tmp_path / "t.db")
    account_id = _upsert(store)
    store.log_event("login", account_id=account_id, detail="strategy=password")
    events = store.recent_events()
    assert events and events[0]["kind"] == "login"
    assert "strategy=password" in events[0]["detail"]
