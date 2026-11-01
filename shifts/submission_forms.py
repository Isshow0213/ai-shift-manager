from django import forms

from accounts.models import StoreMembership


class SubmissionSettingsForm(forms.ModelForm):
    class Meta:
        model = StoreMembership
        fields = ["submission_mode", "default_start_time", "default_end_time"]
        labels = {
            "submission_mode": "シフト提出方式",
            "default_start_time": "基本の開始時刻",
            "default_end_time": "基本の終了時刻",
        }
        widgets = {
            "submission_mode": forms.RadioSelect,
            "default_start_time": forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
            "default_end_time": forms.TimeInput(format="%H:%M", attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        supplied_data = args[0] if args else kwargs.get("data")
        instance = kwargs.get("instance")
        # Older clients only submit the mode. Absence preserves the saved pair;
        # submitting both fields explicitly empty clears it.
        if supplied_data is not None and instance is not None and not any(
            name in supplied_data for name in ("default_start_time", "default_end_time")
        ):
            data = supplied_data.copy()
            for name in ("default_start_time", "default_end_time"):
                value = getattr(instance, name)
                data[name] = value.isoformat() if value else ""
            if args:
                args = (data, *args[1:])
            else:
                kwargs["data"] = data
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if any(name in self.errors for name in ("default_start_time", "default_end_time")):
            return cleaned_data
        start = cleaned_data.get("default_start_time")
        end = cleaned_data.get("default_end_time")
        if (start is None) != (end is None):
            self.add_error(
                "default_start_time" if start is None else "default_end_time",
                "基本時間を設定する場合は、開始・終了時刻の両方を入力してください。",
            )
        elif start is not None and start >= end:
            self.add_error("default_end_time", "終了時刻は開始時刻より後にしてください（日付をまたぐ時間は設定できません）。")
        return cleaned_data
