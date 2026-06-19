from django import forms
from django.contrib.auth import get_user_model

from accounts.models import StoreMembership


User = get_user_model()


class StoreMembershipForm(forms.ModelForm):
    user = forms.ModelChoiceField(
        queryset=User.objects.all(),
        label="従業員",
    )

    class Meta:
        model = StoreMembership
        fields = [
            "user",
            "role",
            "rank",
            "desired_shifts_per_week",
            "is_active",
        ]
        labels = {
            "role": "権限",
            "rank": "ランク",
            "desired_shifts_per_week": "希望勤務回数",
            "is_active": "有効",
        }