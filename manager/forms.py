from django import forms
from django.contrib.auth import get_user_model

from accounts.models import StoreMembership
from shifts.models import Availability, Shift


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

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        if store is not None:
            self.instance.store = store
        self.fields["user"].queryset = User.objects.order_by(
            "last_name", "first_name", "username"
        )
        self.fields["user"].label_from_instance = (
            lambda user: user.full_name_japanese
        )
        self.fields["role"].choices = [
            ("manager", "店長・管理者"),
            ("staff", "従業員"),
        ]
        self.fields["desired_shifts_per_week"].help_text = "1週間あたりの希望勤務回数"

    def clean_user(self):
        user = self.cleaned_data["user"]
        if self.store is not None:
            existing_memberships = StoreMembership.objects.filter(
                user=user, store=self.store
            )
            if self.instance.pk:
                existing_memberships = existing_memberships.exclude(pk=self.instance.pk)
            if existing_memberships.exists():
                raise forms.ValidationError("この従業員はすでに店舗に所属しています。")
        return user


class ShiftForm(forms.ModelForm):
    membership = forms.ModelChoiceField(
        queryset=StoreMembership.objects.none(),
        label="従業員",
    )

    class Meta:
        model = Shift
        fields = [
            "membership",
            "work_date",
            "start_time",
            "end_time",
        ]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }
        labels = {
            "work_date": "日付",
            "start_time": "開始時刻",
            "end_time": "終了時刻",
        }

    def __init__(self, *args, store=None, selected_date=None, **kwargs):
        super().__init__(*args, **kwargs)

        if store is not None and selected_date is not None:
            membership_ids = Availability.objects.filter(
                membership__store=store,
                work_date=selected_date,
            ).values_list(
                "membership_id",
                flat=True,
            ).distinct()

            self.fields["membership"].queryset = StoreMembership.objects.filter(
                id__in=membership_ids,
                is_active=True,
            ).select_related("user")

            self.fields["work_date"].initial = selected_date

        elif store is not None:
            self.fields["membership"].queryset = StoreMembership.objects.filter(
                store=store,
                is_active=True,
            ).select_related("user")

        self.fields["membership"].label_from_instance = (
            lambda obj: obj.user.full_name_japanese
        )
        self.fields["membership"].empty_label = "選択してください"
