"""Minimal HTML form extraction.

The Moodle/Shibboleth/Passport York flow is driven entirely by ordinary HTML
forms, so we need just enough parsing to read ``<form>`` elements and their
``<input>``/``<select>`` children. This avoids a BeautifulSoup dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class FormField:
    name: str
    value: str = ""
    type: str = "text"
    options: list[tuple[str, str]] = field(default_factory=list)

    @property
    def is_password(self) -> bool:
        return self.type.lower() == "password"

    @property
    def is_checkbox(self) -> bool:
        return self.type.lower() == "checkbox"


@dataclass
class Form:
    action: str
    method: str = "post"
    fields: list[FormField] = field(default_factory=list)

    def find(self, name: str) -> FormField | None:
        for f in self.fields:
            if f.name == name:
                return f
        return None

    def has(self, name: str) -> bool:
        return self.find(name) is not None

    @property
    def has_password_field(self) -> bool:
        return any(f.is_password for f in self.fields)

    def hidden_values(self) -> dict[str, str]:
        """Everything except submit buttons, checkboxes and text/password inputs."""
        out: dict[str, str] = {}
        for f in self.fields:
            if not f.name:
                continue
            t = f.type.lower()
            if t in {"submit", "button", "checkbox", "radio", "file", "password"}:
                continue
            if f.is_checkbox:
                continue
            out[f.name] = f.value
        return out

    def payload(
        self, *, skip_types: tuple[str, ...] = ("submit", "button", "image", "checkbox")
    ) -> dict[str, str]:
        """All named fields as a flat dict, suitable for form-encoding.

        Checkboxes are skipped by default because a browser only submits a
        checkbox that is actually ticked — and one of York's boxes is
        "change my Passport York password".
        """
        out: dict[str, str] = {}
        for f in self.fields:
            if not f.name or f.type.lower() in skip_types:
                continue
            out[f.name] = f.value
        return out

    @property
    def hidden_count(self) -> int:
        return len(self.hidden_values())


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[Form] = []
        self._open: Form | None = None
        self._select: FormField | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "form":
            self._open = Form(
                action=a.get("action", ""),
                method=(a.get("method") or "post").lower(),
            )
            self.forms.append(self._open)
        elif tag == "input" and self._open is not None:
            name = a.get("name", "")
            if not name:
                return
            self._open.fields.append(
                FormField(
                    name=name,
                    value=a.get("value", ""),
                    type=(a.get("type") or "text").lower(),
                )
            )
        elif tag == "select" and self._open is not None:
            name = a.get("name", "")
            if not name:
                self._select = None
                return
            f = FormField(name=name, value="", type="select")
            self._open.fields.append(f)
            self._select = f
        elif tag == "option" and self._select is not None:
            val = a.get("value", "")
            label = a.get("label", val)
            self._select.options.append((val, label))
            if "selected" in a or self._select.value == "":
                self._select.value = val
        elif tag == "textarea" and self._open is not None:
            name = a.get("name", "")
            if name:
                self._open.fields.append(FormField(name=name, value="", type="textarea"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._open = None
        elif tag == "select":
            self._select = None


def parse_forms(html: str, base_url: str = "") -> list[Form]:
    """Parse every ``<form>`` in ``html``, resolving relative action URLs."""
    from urllib.parse import urljoin

    parser = _FormParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # malformed upstream HTML should never crash a login
        pass

    for form in parser.forms:
        if base_url and form.action:
            form.action = urljoin(base_url, form.action)
        elif base_url and not form.action:
            form.action = base_url
    return parser.forms


def looks_like_credential_form(forms: list[Form]) -> Form | None:
    """Return the form carrying a password input, if any."""
    for form in forms:
        if form.has_password_field:
            return form
    return None


def pick_progression_form(forms: list[Form]) -> Form | None:
    """Pick the form most likely to advance an SSO conversation.

    Shibboleth's LoginHandler and SAML POST-binding pages are plain forms whose
    only job is to be resubmitted, so the one with the most hidden inputs wins.
    """
    candidates = [f for f in forms if not f.has_password_field and f.hidden_count > 0]
    if not candidates:
        return None
    return max(candidates, key=lambda f: f.hidden_count)
