from datetime import date

from django.db import transaction

from accounts.models import Store
from shifts.models import Availability, Requirement, Shift


@transaction.atomic
def generate_shifts_for_store(store_id, work_date):
    if not isinstance(work_date, date):
        raise ValueError("A work date is required to generate shifts.")

    store = Store.objects.get(id=store_id)
    requirements = list(
        Requirement.objects.filter(store=store, work_date=work_date)
    )
    result = {
        "created_count": 0,
        "shortfall_count": 0,
        "requirement_count": len(requirements),
    }

    if not requirements:
        return result

    # 選択日の自動生成分だけ作り直し、手動分は残す。
    Shift.objects.filter(
        store=store,
        work_date=work_date,
        is_generated=True,
    ).delete()

    scheduled_shifts = list(
        Shift.objects.filter(store=store, work_date=work_date).values_list(
            "user_id", "start_time", "end_time"
        )
    )

    for requirement in requirements:
        covered_users = {
            user_id
            for user_id, start_time, end_time in scheduled_shifts
            if start_time <= requirement.start_time
            and end_time >= requirement.end_time
        }
        remaining_count = max(
            0, requirement.required_staff_count - len(covered_users)
        )

        candidates = Availability.objects.filter(
            membership__store=store,
            membership__role="staff",
            membership__is_active=True,
            membership__user__is_active=True,
            work_date=requirement.work_date,
            start_time__lte=requirement.start_time,
            end_time__gte=requirement.end_time,
        ).select_related("membership").order_by("membership_id", "id")

        for availability in candidates:
            if remaining_count == 0:
                break

            user_id = availability.membership.user_id
            if any(
                scheduled_user_id == user_id
                and start_time < requirement.end_time
                and end_time > requirement.start_time
                for scheduled_user_id, start_time, end_time in scheduled_shifts
            ):
                continue

            Shift.objects.create(
                user_id=user_id,
                membership=availability.membership,
                store=store,
                work_date=requirement.work_date,
                start_time=requirement.start_time,
                end_time=requirement.end_time,
                is_generated=True,
            )
            scheduled_shifts.append(
                (user_id, requirement.start_time, requirement.end_time)
            )
            result["created_count"] += 1
            remaining_count -= 1

        result["shortfall_count"] += remaining_count

    return result
