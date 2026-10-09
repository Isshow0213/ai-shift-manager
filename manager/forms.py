from django import forms
from django.contrib.auth import get_user_model

from accounts.models import StoreMembership
from shifts.models import Availability, Shift


User = get_user_model()


class StaffRankForm(forms.Form):
    rank = forms.ChoiceField(
        label="ランク", choices=StoreMembership.RANK_CHOICES,
        error_messages={
            "required": "ランクを選択してください。",
            "invalid_choice": "ランクはA・B・Cから選んでください。",
        },
    )


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
            "note",
            "display_color",
        ]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "note": forms.Textarea(attrs={"rows": 3, "placeholder": "例：○○店のヘルプ"}),
        }
        labels = {
            "work_date": "日付",
            "start_time": "開始時刻",
            "end_time": "終了時刻",
            "note": "メモ（ヘルプ先など）",
            "display_color": "表示色",
        }
        help_texts = {
            "note": "500文字まで。従業員の確定シフトと全体シフト表に表示されます。",
            "display_color": "ヘルプ先や勤務の種類を見分ける色を選べます。",
        }

    def __init__(self, *args, store=None, selected_date=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store

        if store is not None:
            self.instance.store = store
            memberships = StoreMembership.objects.filter(
                store=store,
                role="staff",
                is_active=True,
                user__is_active=True,
            ).select_related("user")
            if selected_date is not None:
                membership_ids = Availability.objects.filter(
                    membership__store=store,
                    work_date=selected_date,
                ).values_list("membership_id", flat=True)
                memberships = memberships.filter(id__in=membership_ids)
                self.fields["work_date"].initial = selected_date
            self.fields["membership"].queryset = memberships.order_by(
                "user__last_name", "user__first_name", "user__username"
            )

        self.fields["membership"].label_from_instance = (
            lambda obj: obj.user.full_name_japanese
        )
        self.fields["membership"].empty_label = "選択してください"

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        if start_time is not None and end_time is not None:
            if start_time >= end_time:
                self.add_error("end_time", "終了時刻は開始時刻より後にしてください。")
                return cleaned_data

        membership = cleaned_data.get("membership")
        work_date = cleaned_data.get("work_date")
        if membership and work_date and start_time and end_time:
            overlapping_shifts = Shift.objects.filter(
                user=membership.user,
                work_date=work_date,
                start_time__lt=end_time,
                end_time__gt=start_time,
            )
            if self.instance.pk:
                overlapping_shifts = overlapping_shifts.exclude(pk=self.instance.pk)
            if overlapping_shifts.exists():
                self.add_error(
                    None, "この従業員には、同じ時間帯に重なるシフトが登録されています。"
                )
        return cleaned_data
