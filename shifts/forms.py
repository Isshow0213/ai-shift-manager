from django import forms
from .models import Availability, Requirement


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

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        if start_time is not None and end_time is not None and start_time >= end_time:
            raise forms.ValidationError("終了時刻は開始時刻より後にしてください。")
        return cleaned_data

class RequirementForm(forms.ModelForm):
    required_staff_count = forms.IntegerField(
        label="必要人数", min_value=1, max_value=2147483647,
        widget=forms.NumberInput(attrs={"min": 1, "max": 2147483647})
    )

    class Meta:
        model = Requirement
        fields = ["work_date", "start_time", "end_time", "required_staff_count"]
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "required_staff_count": forms.NumberInput(attrs={"min": 1}),
        }
        labels = {
            "work_date": "日付",
            "start_time": "開始時刻",
            "end_time": "終了時刻",
            "required_staff_count": "必要人数",
        }

    def clean(self):
        cleaned_data = super().clean()
        start_time = cleaned_data.get("start_time")
        end_time = cleaned_data.get("end_time")
        if start_time is not None and end_time is not None and start_time >= end_time:
            self.add_error("end_time", "終了時刻は開始時刻より後にしてください。")
        return cleaned_data
