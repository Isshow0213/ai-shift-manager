from uuid import uuid4

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import override


EMAIL_EXISTS_ERROR = "このメールアドレスはすでに使用されています。別のメールアドレスを入力してください。"


def email_is_in_use(email):
    return get_user_model().objects.filter(
        Q(email__iexact=email) | Q(username__iexact=email)
    ).exists()


class StaffInvitationRegistrationForm(forms.Form):
    last_name = forms.CharField(
        label="苗字", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "family-name"})
    )
    first_name = forms.CharField(
        label="名前", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "given-name"})
    )
    email = forms.EmailField(
        label="メールアドレス", max_length=254,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    password1 = forms.CharField(
        label="パスワード", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="パスワード（確認）", strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user_candidate = None
        with override("ja"):
            self.fields["password1"].help_text = password_validation.password_validators_help_text_html()

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if email_is_in_use(email):
            raise forms.ValidationError(EMAIL_EXISTS_ERROR)
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "確認用パスワードが一致しません。")

        self.user_candidate = get_user_model()(
            username=f"staff_{uuid4().hex}",
            last_name=cleaned_data.get("last_name", ""),
            first_name=cleaned_data.get("first_name", ""),
            email=cleaned_data.get("email", ""),
            role="staff", rank="C", desired_shifts_per_week=0,
            is_active=True, is_staff=False, is_superuser=False,
        )
        if password1:
            try:
                with override("ja"):
                    password_validation.validate_password(password1, user=self.user_candidate)
            except ValidationError as error:
                self.add_error("password1", error)
        return cleaned_data

    def save(self):
        if not self.is_valid():
            raise ValueError("Cannot save an invalid registration form.")
        self.user_candidate.set_password(self.cleaned_data["password1"])
        self.user_candidate.save()
        return self.user_candidate
