from django import forms

from accounts.models import CompanyMembership


class ShiftViewerGrantForm(forms.Form):
    company_membership = forms.ModelChoiceField(
        label="権限を設定する会社の所属者",
        queryset=CompanyMembership.objects.none(),
        empty_label="所属者を選択してください",
        error_messages={
            "required": "会社の所属者を選択してください。",
            "invalid_choice": "同じ会社に所属する有効なユーザーを選択してください。",
        },
    )
    can_view_shifts = forms.BooleanField(
        label="全体シフトの閲覧", required=False, initial=True,
    )
    can_submit_availability = forms.BooleanField(
        label="シフト希望の提出", required=False, initial=False,
    )
    show_in_shift_overview = forms.BooleanField(
        label="全体シフト表に表示", required=False, initial=False,
    )

    def __init__(self, *args, store, company_membership_hidden=False, **kwargs):
        supplied_data = args[0] if args else kwargs.get("data")
        if (
            supplied_data is not None and "company_membership" in supplied_data
            and not any(key in supplied_data for key in (
                "permissions_form", "can_view_shifts", "can_submit_availability", "show_in_shift_overview",
            ))
        ):
            data = supplied_data.copy()
            data["can_view_shifts"] = "on"
            if args:
                args = (data, *args[1:])
            else:
                kwargs["data"] = data
        super().__init__(*args, **kwargs)
        field = self.fields["company_membership"]
        field.queryset = CompanyMembership.objects.filter(
            company_id=store.company_id, user__is_active=True,
        ).select_related("user").order_by(
            "user__last_name", "user__first_name", "user__username", "pk",
        )
        field.label_from_instance = lambda membership: membership.user.full_name_japanese
        if company_membership_hidden:
            field.widget = forms.HiddenInput()

    def clean(self):
        cleaned_data = super().clean()
        if not (
            cleaned_data.get("can_view_shifts")
            or cleaned_data.get("can_submit_availability")
            or cleaned_data.get("show_in_shift_overview")
        ):
            raise forms.ValidationError(
                "閲覧・希望提出・表への表示から1つ以上選択してください。"
            )
        return cleaned_data
