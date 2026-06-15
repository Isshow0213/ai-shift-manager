from shifts.models import Availability, Requirement, Shift


def generate_shifts():
    Shift.objects.all().delete()

    requirements = Requirement.objects.all()

    for requirement in requirements:
        candidates = Availability.objects.filter(
            work_date=requirement.work_date,
            start_time__lte=requirement.start_time,
            end_time__gte=requirement.end_time,
        )

        selected_users = candidates[: requirement.required_staff_count]

        for availability in selected_users:
            Shift.objects.create(
                user=availability.user,
                work_date=requirement.work_date,
                start_time=requirement.start_time,
                end_time=requirement.end_time,
                is_generated=True,
            )


