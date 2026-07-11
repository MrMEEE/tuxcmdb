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
    attribute_name = forms.CharField(max_length=120, widget=forms.TextInput(attrs=TEXT_INPUT))
    value = forms.CharField(max_length=255, required=False, widget=forms.TextInput(attrs=TEXT_INPUT))


class AttributeForm(forms.Form):
    name = forms.CharField(max_length=120, widget=forms.TextInput(attrs=TEXT_INPUT))
    data_type = forms.CharField(max_length=32, widget=forms.TextInput(attrs=TEXT_INPUT))
    description = forms.CharField(widget=forms.Textarea(attrs=TEXTAREA_INPUT), required=False)
    allow_multiple = forms.BooleanField(required=False)


class APIUserForm(forms.Form):
    username = forms.CharField(max_length=120, widget=forms.TextInput(attrs=TEXT_INPUT))
    password = forms.CharField(widget=forms.PasswordInput(render_value=True, attrs=PASSWORD_INPUT), required=False)
    is_active = forms.BooleanField(required=False, initial=True)
    readonly = forms.BooleanField(required=False)


class DatatypeForm(forms.Form):
    name = forms.CharField(max_length=32, widget=forms.TextInput(attrs=TEXT_INPUT))
    description = forms.CharField(widget=forms.Textarea(attrs=TEXTAREA_INPUT), required=False)
    builtin_validator = forms.CharField(max_length=32, required=False, widget=forms.TextInput(attrs=TEXT_INPUT))
    regex_pattern = forms.CharField(widget=forms.Textarea(attrs={**TEXTAREA_INPUT, "rows": 2}), required=False)
