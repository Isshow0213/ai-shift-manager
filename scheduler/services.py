from accounts.models import Store
from shifts.models import Availability, Requirement, Shift


def generate_shifts_for_store(store_id):
    store = Store.objects.get(id=store_id)

    # その店舗の既存の自動生成シフトだけ削除
    Shift.objects.filter(
        store=store,
        is_generated=True,
    ).delete()

    requirements = Requirement.objects.filter(store=store)

    for requirement in requirements:
        candidates = Availability.objects.filter(
            membership__store=store,
            work_date=requirement.work_date,
            start_time__lte=requirement.start_time,
            end_time__gte=requirement.end_time,
        ).select_related("membership", "membership__user")

        selected_availabilities = candidates[: requirement.required_staff_count]

        for availability in selected_availabilities:
            Shift.objects.create(
                user=availability.membership.user,
                membership=availability.membership,
                store=store,
                work_date=requirement.work_date,
                start_time=requirement.start_time,
                end_time=requirement.end_time,
                is_generated=True,
            )