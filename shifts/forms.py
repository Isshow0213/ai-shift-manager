from django import forms
from django.contrib.auth import get_user_model
from .models import Availability, Requirement, StoreOperatingHours


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

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        work_date = cleaned_data.get("work_date")
        
        if start_time is not None and end_time is not None and start_time >= end_time:
            raise forms.ValidationError("終了時刻は開始時刻より後にしてください。")
        
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
            "deadline",
            "day_type",
            "memo",
        ]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "required_staff_count": forms.NumberInput(attrs={"min": 1}),
            "deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "day_type": forms.Select(),
            "memo": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "work_date": "日付",
            "start_time": "開始時刻",
            "end_time": "終了時刻",
            "required_staff_count": "必要人数",
            "deadline": "締切",
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
