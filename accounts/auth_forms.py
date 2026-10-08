from django import forms
from django.contrib.auth.forms import AuthenticationForm, UsernameField


class EmailOrUsernameAuthenticationForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "メールアドレスまたはユーザー名とパスワードを確認してください。",
        "inactive": "このアカウントは無効になっています。",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"] = UsernameField(
            label="メールアドレスまたはユーザー名", max_length=254,
            widget=forms.TextInput(attrs={"autocomplete": "username", "autofocus": True}),
        )
