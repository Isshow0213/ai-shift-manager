from django import forms
from django.contrib.auth import get_user_model
from .models import Availability, Requirement, StoreOperatingHours, StoreSubmissionDeadline
from .submission_deadlines import get_submission_period, is_submission_closed
from .requirement_bulk_forms import RequirementTimeSlotForm
from .requirement_settings import get_month_dates


User = get_user_model()


class ShiftGenerationForm(forms.Form):
    work_date = forms.DateField(input_formats=["%Y-%m-%d"])


class AvailabilityForm(forms.ModelForm):
    class Meta:
        model = Availability
        fields = ["work_date", "start_time", "end_time"]
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

    def __init__(self, *args, user=None, submission_deadline=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.submission_deadline = submission_deadline

    def clean_work_date(self):
        work_date = self.cleaned_data["work_date"]
        if not 2 <= work_date.year <= 9998:
            raise forms.ValidationError("日付は2年〜9998年の範囲で指定してください。")
        return work_date

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        work_date = cleaned_data.get("work_date")
        
        if start_time is not None and end_time is not None and start_time >= end_time:
            raise forms.ValidationError("終了時刻は開始時刻より後にしてください。")

        if work_date and is_submission_closed(self.submission_deadline, work_date):
            period = get_submission_period(self.submission_deadline, work_date)
            raise forms.ValidationError(
                f"この日の希望提出は{period.deadline_date:%Y/%m/%d} 23:59で締め切りました。"
            )
        
        # Check for overlapping availabilities on the same day for the current user
        if self.user and work_date and start_time and end_time:
            overlapping = Availability.objects.filter(
                user=self.user,
                work_date=work_date,
            )
            
            # Exclude the current instance if we're editing
            if self.instance.pk:
                overlapping = overlapping.exclude(pk=self.instance.pk)
            
            for availability in overlapping:
                # Check if time slots overlap
                if not (end_time <= availability.start_time or start_time >= availability.end_time):
                    raise forms.ValidationError(
                        f"この日付の {availability.start_time.strftime('%H:%M')}〜{availability.end_time.strftime('%H:%M')} "
                        f"と重複しています。"
                    )
        
        return cleaned_data


class RequirementForm(forms.ModelForm):
    required_staff_count = forms.IntegerField(
        label="必要人数", min_value=1, max_value=2147483647,
        widget=forms.NumberInput(attrs={"min": 1, "max": 2147483647})
    )

    class Meta:
        model = Requirement
        fields = [
            "work_date",
            "start_time",
            "end_time",
            "required_staff_count",
            "day_type",
            "memo",
        ]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "required_staff_count": forms.NumberInput(attrs={"min": 1}),
            "day_type": forms.Select(),
            "memo": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "work_date": "日付",
            "start_time": "開始時刻",
            "end_time": "終了時刻",
            "required_staff_count": "必要人数",
            "day_type": "曜日種別",
            "memo": "メモ",
        }

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        if start_time is not None and end_time is not None and start_time >= end_time:
            self.add_error("end_time", "終了時刻は開始時刻より後にしてください。")
        return cleaned_data


class ManagerRequirementForm(RequirementTimeSlotForm):
    schedule_mode = forms.ChoiceField(
        label="設定方法", choices=[("month", "月・曜日種別でまとめて設定"), ("date", "日付を指定して設定")],
        initial="month",
    )
    work_date = forms.DateField(label="日付", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    target_month = forms.DateField(
        label="対象月", required=False, input_formats=["%Y-%m"],
        widget=forms.DateInput(format="%Y-%m", attrs={"type": "month", "min": "1949-01", "max": "2099-12"}),
    )
    day_type = forms.ChoiceField(
        label="曜日種別", required=False, choices=[("", "選択してください"), *Requirement.DAY_TYPE_CHOICES],
    )
    memo = forms.CharField(label="メモ", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        supplied_data = args[0] if args else kwargs.get("data")
        if supplied_data is not None and "schedule_mode" not in supplied_data:
            data = supplied_data.copy()
            data["schedule_mode"] = "month" if data.get("target_month") or not data.get("work_date") else "date"
            if args:
                args = (data, *args[1:])
            else:
                kwargs["data"] = data
        super().__init__(*args, **kwargs)
        mode = self.data.get("schedule_mode") if self.is_bound else self.initial.get("schedule_mode", "month")
        if mode in ("month", "date"):
            for field in ("work_date", "target_month", "day_type"):
                enabled = field == "work_date" if mode == "date" else field != "work_date"
                self.fields[field].required = enabled
                self.fields[field].disabled = not enabled

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("schedule_mode")
        if mode == "month":
            target_month = cleaned_data.get("target_month")
            day_type = cleaned_data.get("day_type")
            if target_month and not 1949 <= target_month.year <= 2099:
                self.add_error("target_month", "対象月は1949年〜2099年の範囲で指定してください。")
            elif target_month and day_type:
                dates = get_month_dates(target_month, day_type)
                if not dates:
                    self.add_error("day_type", "この月には選択した曜日種別の日がありません。")
                cleaned_data["work_dates"] = dates
        elif mode == "date" and cleaned_data.get("work_date"):
            if not 2 <= cleaned_data["work_date"].year <= 9998:
                self.add_error("work_date", "日付は2年〜9998年の範囲で指定してください。")
            else:
                cleaned_data["work_dates"] = [cleaned_data["work_date"]]
        return cleaned_data


class StoreOperatingHoursForm(forms.ModelForm):
    start_time = forms.TimeField(
        label="開始時刻",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
        error_messages={"required": "開始時刻を入力してください。"},
    )
    end_time = forms.TimeField(
        label="終了時刻",
        widget=forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
        error_messages={"required": "終了時刻を入力してください。"},
    )

    class Meta:
        model = StoreOperatingHours
        fields = ["start_time", "end_time"]

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("auto_id", "operating-hours-%s")
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        if start_time is not None and end_time is not None and start_time >= end_time:
            self.add_error("end_time", "終了時刻は開始時刻より後にしてください。")
        return cleaned_data


class SubmissionDeadlineForm(forms.ModelForm):
    weekly_deadline_weekday = forms.TypedChoiceField(
        label="締切曜日", coerce=int, required=False, empty_value=None,
        choices=[(index, f"{label}曜日") for index, label in enumerate("月火水木金土日")],
    )
    monthly_deadline_day = forms.IntegerField(
        label="前月の締切日", min_value=1, max_value=31, required=False,
        widget=forms.NumberInput(attrs={"min": 1, "max": 31}),
    )

    class Meta:
        model = StoreSubmissionDeadline
        fields = ["mode", "weekly_deadline_weekday", "monthly_deadline_day"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in ("weekly_deadline_weekday", "monthly_deadline_day"):
            self.initial.setdefault(field, getattr(self.instance, field))
        mode = self.data.get("mode") if self.is_bound else self.initial.get("mode", self.instance.mode)
        if mode in StoreSubmissionDeadline.Mode.values:
            weekly = mode == StoreSubmissionDeadline.Mode.WEEKLY
            self.fields["weekly_deadline_weekday"].required = weekly
            self.fields["weekly_deadline_weekday"].disabled = not weekly
            self.fields["monthly_deadline_day"].required = not weekly
            self.fields["monthly_deadline_day"].disabled = weekly

    def clean(self):
        cleaned_data = super().clean()
        mode = cleaned_data.get("mode")
        required_field = (
            "weekly_deadline_weekday" if mode == StoreSubmissionDeadline.Mode.WEEKLY
            else "monthly_deadline_day" if mode == StoreSubmissionDeadline.Mode.MONTHLY else None
        )
        if required_field and cleaned_data.get(required_field) is None and required_field not in self.errors:
            self.add_error(required_field, "締切の曜日または日付を選択してください。")
        for field, default in [("weekly_deadline_weekday", 2), ("monthly_deadline_day", 15)]:
            if field != required_field and cleaned_data.get(field) is None and field not in self.errors:
                cleaned_data[field] = getattr(self.instance, field, default)
        return cleaned_data
