from django import forms
import ipaddress
import re
from urllib.parse import urlsplit

from tuxcmdb.hypervisors import list_supported_hypervisor_types


TEXT_INPUT = {"class": "form-control"}
PASSWORD_INPUT = {"class": "form-control"}
TEXTAREA_INPUT = {"class": "form-control", "rows": 3}


class LoginForm(forms.Form):
    username = forms.CharField(max_length=120, widget=forms.TextInput(attrs=TEXT_INPUT))
    password = forms.CharField(widget=forms.PasswordInput(attrs=PASSWORD_INPUT))


class AssetCreateForm(forms.Form):
    assetname = forms.CharField(max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT))


class AssetUpdateForm(forms.Form):
    assetname = forms.CharField(max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT))


class AssignmentForm(forms.Form):
    attribute_name = forms.ChoiceField(choices=(), widget=forms.Select(attrs=TEXT_INPUT))
    value = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs=TEXT_INPUT))

    def __init__(self, *args, attribute_choices: list[tuple[str, str]] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "Select attribute")]
        if attribute_choices:
            choices.extend(attribute_choices)
        self.fields["attribute_name"].choices = choices


class AttributeForm(forms.Form):
    name = forms.CharField(max_length=120, widget=forms.TextInput(attrs=TEXT_INPUT))
    data_type = forms.ChoiceField(choices=(), widget=forms.Select(attrs=TEXT_INPUT))
    description = forms.CharField(widget=forms.Textarea(attrs=TEXTAREA_INPUT), required=False)
    allow_multiple = forms.BooleanField(required=False)
    inventory_group = forms.BooleanField(required=False)

    def __init__(self, *args, datatype_choices: list[tuple[str, str]] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["data_type"].choices = datatype_choices or []


class APIUserForm(forms.Form):
    username = forms.CharField(max_length=120, widget=forms.TextInput(attrs=TEXT_INPUT))
    name = forms.CharField(max_length=120, required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    description = forms.CharField(widget=forms.Textarea(attrs=TEXTAREA_INPUT), required=False)
    password = forms.CharField(widget=forms.PasswordInput(render_value=True, attrs=PASSWORD_INPUT), required=False)
    password_verify = forms.CharField(
        widget=forms.PasswordInput(render_value=True, attrs=PASSWORD_INPUT),
        required=False,
        label="Verify password",
    )
    is_active = forms.BooleanField(required=False, initial=True)
    readonly = forms.BooleanField(required=False)

    def __init__(self, *args, require_password: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.require_password = require_password

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password") or ""
        password_verify = cleaned.get("password_verify") or ""

        if self.require_password and not password:
            self.add_error("password", "Password is required for new API users")

        if password and not password_verify:
            self.add_error("password_verify", "Please verify the password")

        if password and password_verify and password != password_verify:
            self.add_error("password_verify", "Passwords do not match")

        return cleaned


class DatatypeForm(forms.Form):
    VALIDATION_MODE_CHOICES = (
        ("none", "None"),
        ("builtin", "Builtin validator"),
        ("regex", "Regex pattern"),
        ("allowed_values", "Allowed values list"),
    )

    name = forms.CharField(max_length=32, widget=forms.TextInput(attrs=TEXT_INPUT))
    description = forms.CharField(widget=forms.Textarea(attrs=TEXTAREA_INPUT), required=False)
    validation_mode = forms.ChoiceField(choices=VALIDATION_MODE_CHOICES, initial="none", widget=forms.RadioSelect)
    builtin_validator = forms.ChoiceField(choices=(), required=False, widget=forms.Select(attrs=TEXT_INPUT))
    regex_pattern = forms.CharField(widget=forms.Textarea(attrs={**TEXTAREA_INPUT, "rows": 2}), required=False)

    def __init__(self, *args, builtin_validator_choices: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [("", "None")]
        choices.extend((value, value) for value in (builtin_validator_choices or []))
        self.fields["builtin_validator"].choices = choices


class OperatingSystemForm(forms.Form):
    name = forms.CharField(max_length=120, widget=forms.TextInput(attrs=TEXT_INPUT))
    description = forms.CharField(widget=forms.Textarea(attrs=TEXTAREA_INPUT), required=False)
    aliases = forms.CharField(widget=forms.Textarea(attrs={**TEXTAREA_INPUT, "rows": 2}), required=False)


class HypervisorClusterForm(forms.Form):
    name = forms.CharField(max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT))
    cluster_type = forms.ChoiceField(choices=(), widget=forms.Select(attrs=TEXT_INPUT), initial="vmware")
    management_hostname = forms.CharField(max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT))
    username = forms.CharField(max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT))
    password = forms.CharField(widget=forms.PasswordInput(render_value=True, attrs=PASSWORD_INPUT))
    attribute_id = forms.ChoiceField(choices=(), required=False, widget=forms.Select(attrs=TEXT_INPUT))
    verify_ssl = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, attribute_choices: list[tuple[str, str]] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = [(item["key"], item["label"]) for item in list_supported_hypervisor_types()]
        if not choices:
            choices = [("vmware", "VMware"), ("xenserver", "XenServer/XCP-ng")]
        self.fields["cluster_type"].choices = choices
        self.fields["attribute_id"].choices = [("", "Select an attribute")] + (attribute_choices or [])
        if not self.initial.get("cluster_type"):
            self.initial["cluster_type"] = choices[0][0] if choices else "vmware"


class LDAPSourceForm(forms.Form):
    name = forms.CharField(max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT))
    hostname = forms.CharField(max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT))
    port = forms.IntegerField(min_value=1, max_value=65535, widget=forms.NumberInput(attrs=TEXT_INPUT), initial=389)
    protocol = forms.ChoiceField(
        choices=[("ldap", "ldap"), ("ldaps", "ldaps")],
        widget=forms.Select(attrs=TEXT_INPUT),
        initial="ldap",
    )
    verify_certs = forms.BooleanField(required=False, initial=True)
    server_type = forms.ChoiceField(
        choices=[("ad", "ad"), ("openldap", "openldap")],
        widget=forms.Select(attrs=TEXT_INPUT),
        initial="ad",
    )
    bind_dn = forms.CharField(max_length=512, required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    bind_password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=True, attrs=PASSWORD_INPUT))
    base_dn = forms.CharField(max_length=512, widget=forms.TextInput(attrs=TEXT_INPUT))
    group_base_dn = forms.CharField(max_length=512, required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    group_membership = forms.ChoiceField(
        choices=[("ad", "ad"), ("memberof", "memberof")],
        widget=forms.Select(attrs=TEXT_INPUT),
        initial="ad",
    )
    ldap_filter = forms.CharField(max_length=512, initial="(objectClass=person)", widget=forms.TextInput(attrs=TEXT_INPUT))
    attr_username = forms.CharField(max_length=64, initial="sAMAccountName", widget=forms.TextInput(attrs=TEXT_INPUT))
    attr_first_name = forms.CharField(max_length=64, initial="givenName", widget=forms.TextInput(attrs=TEXT_INPUT))
    attr_last_name = forms.CharField(max_length=64, initial="sn", widget=forms.TextInput(attrs=TEXT_INPUT))
    attr_email = forms.CharField(max_length=64, initial="mail", widget=forms.TextInput(attrs=TEXT_INPUT))
    is_active = forms.BooleanField(required=False, initial=True)


class LDAPUserTestForm(forms.Form):
    username = forms.CharField(max_length=255, widget=forms.TextInput(attrs=TEXT_INPUT))
    password = forms.CharField(required=True, widget=forms.PasswordInput(render_value=False, attrs=PASSWORD_INPUT))


class LDAPUserAccessForm(forms.Form):
    readonly = forms.TypedChoiceField(
        choices=[("1", "Readonly"), ("0", "Read/Write")],
        coerce=lambda value: str(value) == "1",
        widget=forms.Select(attrs=TEXT_INPUT),
    )
    is_active = forms.TypedChoiceField(
        choices=[("1", "Active"), ("0", "Disabled")],
        coerce=lambda value: str(value) == "1",
        widget=forms.Select(attrs=TEXT_INPUT),
    )


class LDAPGroupRoleMappingForm(forms.Form):
    source_id = forms.ChoiceField(choices=(), widget=forms.Select(attrs=TEXT_INPUT))
    group_name = forms.CharField(max_length=512, widget=forms.TextInput(attrs=TEXT_INPUT))
    readonly = forms.TypedChoiceField(
        choices=[("1", "Readonly"), ("0", "Read/Write")],
        coerce=lambda value: str(value) == "1",
        widget=forms.Select(attrs=TEXT_INPUT),
    )
    is_active = forms.TypedChoiceField(
        choices=[("1", "Active"), ("0", "Disabled")],
        coerce=lambda value: str(value) == "1",
        widget=forms.Select(attrs=TEXT_INPUT),
    )

    def __init__(self, *args, source_choices: list[tuple[str, str]] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_id"].choices = source_choices or []
class AgentBuildSettingsForm(forms.Form):
    versions_to_keep = forms.IntegerField(
        label="Versions to keep", min_value=1, initial=3,
        help_text="Includes the current version. Older versions are removed by the updater when no build is active.",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )


class AgentInstallForm(forms.Form):
    hosts = forms.CharField(
        label="Hostnames or IP addresses", max_length=13000,
        help_text="Separate hostnames, IPv4 or IPv6 addresses with spaces, commas or new lines (up to 50).",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    username = forms.CharField(max_length=128, widget=forms.TextInput(attrs=TEXT_INPUT))
    password = forms.CharField(
        max_length=1024, strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    server_url = forms.CharField(
        label="Server hostname / API URL", max_length=500,
        help_text="The API address reachable from the target hosts. HTTPS is used if no scheme is supplied.",
        widget=forms.TextInput(attrs=TEXT_INPUT),
    )
    use_sudo = forms.BooleanField(required=False, label="Use sudo")
    ignore_ssl = forms.BooleanField(required=False, label="Ignore SSL verification")

    def clean_hosts(self):
        hosts = []
        for value in re.split(r"[\s,]+", self.cleaned_data["hosts"].strip()):
            try:
                address = ipaddress.ip_address(value)
            except ValueError:
                hostname = value.removesuffix(".").lower()
                labels = hostname.split(".")
                if (len(hostname) > 253 or re.fullmatch(r"[0-9.]+", hostname)
                        or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                               for label in labels)):
                    raise forms.ValidationError(f"Invalid hostname or IP address: {value}")
                host = hostname
            else:
                if address.is_unspecified or address.is_multicast or "%" in value:
                    raise forms.ValidationError("Use unicast IP addresses without scope identifiers.")
                host = str(address)
            if host not in hosts:
                hosts.append(host)
        if len(hosts) > 50:
            raise forms.ValidationError("Install on at most 50 hosts per request.")
        return hosts

    def clean_password(self):
        password = self.cleaned_data["password"]
        if "\n" in password or "\r" in password or "\0" in password:
            raise forms.ValidationError("The SSH/sudo password cannot contain line breaks or null bytes.")
        return password

    def clean_username(self):
        username = self.cleaned_data["username"]
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.@-]*", username):
            raise forms.ValidationError("Enter a valid SSH username.")
        return username

    def clean_server_url(self):
        value = self.cleaned_data["server_url"]
        if "://" not in value:
            value = "https://" + value
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise forms.ValidationError("Invalid server hostname or port.") from exc
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or re.search(r"[\s\x00-\x1f]", value)
                or port == 0):
            raise forms.ValidationError("Enter an HTTP(S) server hostname with optional port, without a path or credentials.")
        return value.rstrip("/")
