from django import forms


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
    name = forms.CharField(max_length=32, widget=forms.TextInput(attrs=TEXT_INPUT))
    description = forms.CharField(widget=forms.Textarea(attrs=TEXTAREA_INPUT), required=False)
    builtin_validator = forms.CharField(max_length=32, required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    regex_pattern = forms.CharField(widget=forms.Textarea(attrs={**TEXTAREA_INPUT, "rows": 2}), required=False)


class OperatingSystemForm(forms.Form):
    name = forms.CharField(max_length=120, widget=forms.TextInput(attrs=TEXT_INPUT))
    description = forms.CharField(widget=forms.Textarea(attrs=TEXTAREA_INPUT), required=False)
    aliases = forms.CharField(widget=forms.Textarea(attrs={**TEXTAREA_INPUT, "rows": 2}), required=False)
