from django import forms
from django.contrib.auth import get_user_model
from django.core import signing
from django.db.models import Q

from accounts.models import StoreMembership
from shifts.models import Availability, Shift
from shifts.eligibility import eligible_membership_ids, get_eligibility


User = get_user_model()


class StaffingWarningForm(forms.Form):
    work_date = forms.DateField(input_formats=["%Y-%m-%d"])
    action = forms.ChoiceField(choices=[("dismiss", "解除"), ("restore", "再表示")])
    staffing_signature = forms.RegexField(regex=r"\A[0-9a-f]{64}\Z")

    def clean_work_date(self):
        work_date = self.cleaned_data["work_date"]
        if not 2 <= work_date.year <= 9998:
            raise forms.ValidationError("日付は2年〜9998年の範囲で指定してください。")
        return work_date


class StaffRankForm(forms.Form):
    rank = forms.ChoiceField(
        label="ランク", choices=StoreMembership.RANK_CHOICES,
        error_messages={
            "required": "ランクを選択してください。",
            "invalid_choice": "ランクはA・B・Cから選んでください。",
        },
    )


class StaffDesiredShiftsForm(forms.Form):
    desired_shifts_per_week = forms.IntegerField(
        label="希望出勤数／週", min_value=0, max_value=7,
        widget=forms.NumberInput(attrs={"min": 0, "max": 7, "step": 1, "inputmode": "numeric"}),
        error_messages={
            "required": "希望出勤数を入力してください。",
            "invalid": "希望出勤数は整数で入力してください。",
            "min_value": "希望出勤数は0回以上で入力してください。",
            "max_value": "希望出勤数は週7回以下で入力してください。",
        },
    )


class StaffPermissionsForm(forms.Form):
    can_view_shifts = forms.BooleanField(
        label="全体シフトを閲覧", required=False,
    )
    can_submit_availability = forms.BooleanField(
        label="希望を提出", required=False,
    )
    show_in_shift_overview = forms.BooleanField(
        label="全体シフト表に表示", required=False,
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
            "can_view_shifts",
            "can_submit_availability",
            "show_in_shift_overview",
        ]
        labels = {
            "role": "権限",
            "rank": "ランク",
            "desired_shifts_per_week": "希望勤務回数",
            "is_active": "有効",
            "can_view_shifts": "全体シフトを閲覧",
            "can_submit_availability": "希望を提出",
            "show_in_shift_overview": "全体シフト表に表示",
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

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("role") == "manager":
            for field_name in StaffPermissionsForm.base_fields:
                cleaned_data[field_name] = True
        return cleaned_data


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

    def __init__(
        self, *args, store=None, selected_date=None,
        only_available_staff=True, include_managers=False, **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.store = store

        if store is not None:
            self.instance.store = store
            memberships = StoreMembership.objects.filter(
                store=store,
                role__in=["staff", "manager"] if include_managers else ["staff"],
                is_active=True,
                user__is_active=True,
            ).select_related("user")
            if selected_date is not None:
                if only_available_staff:
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


class OverviewShiftForm(ShiftForm):
    confirmation_salt = "manager.overview.outside-confirmation"
    confirmation_max_age = 15 * 60

    confirm_outside = forms.BooleanField(
        label="本人に確認し、この希望外の日時で登録することを確認しました",
        required=False,
    )
    outside_confirmation_token = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(
            *args, store=store, include_managers=True,
            only_available_staff=False, **kwargs,
        )
        self.outside_confirmation_required = False
        self.outside_confirmation_message = ""
        if self.is_bound:
            self.data = self.data.copy()
        # An existing shift remains editable after its employee is deactivated.
        # Inactive employees cannot be chosen for a new shift or reassignment.
        if store is not None and self.instance.pk and self.instance.membership_id:
            self.fields["membership"].queryset = StoreMembership.objects.filter(
                Q(is_active=True, user__is_active=True, role__in=["staff", "manager"])
                | Q(pk=self.instance.membership_id),
                store=store,
            ).select_related("user", "store").order_by(
                "user__last_name", "user__first_name", "user__username",
            )
        self.fields["work_date"].widget.attrs.update(min="0002-01-01", max="9998-12-31")

    def clean_work_date(self):
        work_date = self.cleaned_data["work_date"]
        if not 2 <= work_date.year <= 9998:
            raise forms.ValidationError("日付は2年〜9998年の範囲で指定してください。")
        return work_date

    def clean(self):
        cleaned_data = super().clean()
        # Show the outside-wish confirmation only after the normal input and
        # overlap checks succeed. It must never stand in for these checks.
        if self.errors:
            return cleaned_data
        membership = cleaned_data.get("membership")
        work_date = cleaned_data.get("work_date")
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        if membership and work_date and start_time and end_time and start_time < end_time:
            eligibility = get_eligibility(membership, work_date)
            if eligibility.covers(start_time, end_time):
                return cleaned_data

            payload = {
                "store": self.store.pk,
                "membership": membership.pk,
                "date": work_date.isoformat(),
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
                "shift": self.instance.pk,
            }
            token = cleaned_data.get("outside_confirmation_token", "")
            try:
                confirmed_payload = signing.loads(
                    token, salt=self.confirmation_salt, max_age=self.confirmation_max_age,
                )
            except (signing.BadSignature, ValueError, TypeError):
                confirmed_payload = None
            if confirmed_payload == payload and cleaned_data.get("confirm_outside"):
                return cleaned_data

            self.outside_confirmation_required = True
            wished = "、".join(f"{start:%H:%M}〜{end:%H:%M}" for start, end in eligibility.intervals)
            reason = (
                f"希望時間：{wished}。" if wished else {
                    "not_submitted": "この月の希望は未提出です。",
                    "day_off": "この日は休み希望です。",
                    "no_availability": "この日の出勤希望はありません。",
                    "inactive": "この従業員は現在無効です。",
                }.get(eligibility.reason, "この日時の出勤希望はありません。")
            )
            self.outside_confirmation_message = (
                f"{membership.user.full_name_japanese}さんの{work_date.year}年{work_date.month}月{work_date.day}日 "
                f"{start_time:%H:%M}〜{end_time:%H:%M}は希望外です。{reason}"
                "本人に確認したうえで、この日時で保存してください。"
            )
            self.data[self.add_prefix("outside_confirmation_token")] = signing.dumps(
                payload, salt=self.confirmation_salt,
            )
            # Changing the employee/date/time invalidates the previous consent.
            if confirmed_payload != payload:
                self.data[self.add_prefix("confirm_outside")] = ""
            self.add_error("confirm_outside", "確認後にチェックして、もう一度保存してください。")
        return cleaned_data


class ShiftCreationForm(ShiftForm):
    requires_availability = True

    confirm_outside_availability = forms.BooleanField(
        label="希望時間外の登録確認", required=False, initial=False,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, selected_date=None, **kwargs):
        self.selected_date = selected_date
        kwargs["include_managers"] = True
        kwargs["only_available_staff"] = False
        super().__init__(*args, selected_date=selected_date, **kwargs)
        if (
            self.requires_availability
            and self.store is not None
            and selected_date is not None
        ):
            self.fields["membership"].queryset = self.fields["membership"].queryset.filter(
                pk__in=eligible_membership_ids(self.store, selected_date),
            )

    def clean_work_date(self):
        work_date = self.cleaned_data["work_date"]
        if not 2 <= work_date.year <= 9998:
            raise forms.ValidationError("日付は2年〜9998年の範囲で指定してください。")
        if self.selected_date is not None and work_date != self.selected_date:
            raise forms.ValidationError("カレンダーで選択した日付のシフトを追加してください。")
        return work_date

    def clean(self):
        cleaned_data = super().clean()
        if not self.requires_availability:
            return cleaned_data
        membership = cleaned_data.get("membership")
        work_date = cleaned_data.get("work_date")
        start_time, end_time = cleaned_data.get("start_time"), cleaned_data.get("end_time")
        if membership and work_date and start_time and end_time and start_time < end_time:
            eligibility = get_eligibility(membership, work_date)
            if not eligibility.available:
                self.add_error("membership", "未提出・休み希望・出勤不可の人には登録できません。")
            elif not eligibility.covers(start_time, end_time) and not cleaned_data.get("confirm_outside_availability"):
                wish_times = "、".join(f"{start:%H:%M}〜{end:%H:%M}" for start, end in eligibility.intervals)
                self.add_error(None, (
                    f"希望時間外のシフトです。{membership.user.full_name_japanese}さんの希望時間は{wish_times}ですが、"
                    f"{start_time:%H:%M}〜{end_time:%H:%M}で登録しようとしています。本人への確認後、登録を確認してください。"
                ))
        return cleaned_data


class AvailabilityShiftForm(ShiftCreationForm):
    pass


class OutsideAvailabilityShiftForm(ShiftCreationForm):
    requires_availability = False

    confirm_outside_availability = forms.BooleanField(
        label="希望外の登録確認", required=True, initial=False,
        widget=forms.HiddenInput(),
        error_messages={
            "required": "希望外のシフトを追加するには、本人への確認後、確認画面で承認してください。",
        },
    )
