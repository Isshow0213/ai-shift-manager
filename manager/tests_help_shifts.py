from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.html import escape

from accounts.models import Company, Store, StoreMembership
from shifts.models import HelpShift, Shift


class HelpShiftTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        company = Company.objects.create(name="ヘルプ入力テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        users = get_user_model()
        cls.manager = users.objects.create_user(username="help_manager")
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager",
        )
        cls.staff = users.objects.create_user(
            username="help_staff", last_name="田中", first_name="花子",
        )
        cls.membership = StoreMembership.objects.create(user=cls.staff, store=cls.store)
        cls.foreign_manager = users.objects.create_user(username="help_foreign_manager")
        StoreMembership.objects.create(
            user=cls.foreign_manager, store=cls.other_store, role="manager",
        )
        cls.work_date = date(2030, 11, 12)

    def setUp(self):
        self.client.force_login(self.manager)
        self.calendar_url = self.calendar_for(self.work_date)
        self.month_query = {"year": self.work_date.year, "month": self.work_date.month}

    def calendar_for(self, work_date):
        return (
            f"{reverse('manager_shift_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def help_data(self, *, prefixed=True, **overrides):
        fields = {
            "name": "山田 太郎（他店ヘルプ）",
            "work_date": self.work_date.isoformat(),
            "start_time": "09:00", "end_time": "18:00",
            "note": "東店からヘルプ、レジ担当", "display_color": "orange",
        }
        fields.update(overrides)
        if prefixed:
            return {"shift_mode": "help", **{f"help-{key}": value for key, value in fields.items()}}
        return fields

    def make_help(self, **overrides):
        fields = {
            "store": self.store, "name": "山田 太郎（他店ヘルプ）",
            "work_date": self.work_date, "start_time": time(9), "end_time": time(18),
            "note": "東店からヘルプ、レジ担当", "display_color": "orange",
        }
        fields.update(overrides)
        return HelpShift.objects.create(**fields)

    def edit_url(self, help_shift):
        return reverse("manager_help_shift_edit", args=[help_shift.pk])

    def delete_url(self, help_shift):
        return reverse("manager_help_shift_delete", args=[help_shift.pk])

    def snapshots(self):
        return {
            "help": list(HelpShift.objects.order_by("pk").values()),
            "users": list(get_user_model().objects.order_by("pk").values()),
            "memberships": list(StoreMembership.objects.order_by("pk").values()),
            "shifts": list(Shift.objects.order_by("pk").values()),
        }

    def test_calendar_has_separate_help_form_with_selected_date_and_csrf(self):
        response = self.client.get(self.calendar_url)

        self.assertEqual(response.status_code, 200)
        form = response.context["help_form"]
        self.assertFalse(form.is_bound)
        self.assertEqual(form.prefix, "help")
        self.assertEqual(form["work_date"].value(), self.work_date)
        self.assertEqual(set(form.fields), {
            "name", "work_date", "start_time", "end_time", "note", "display_color",
        })
        self.assertContains(response, 'name="help-name"')
        self.assertContains(response, 'name="help-note"')
        self.assertContains(response, 'name="shift_mode" value="help"')
        self.assertContains(response, 'name="csrfmiddlewaretoken"')

    def test_help_addition_saves_name_times_note_color_without_employee_registration(self):
        before = self.snapshots()
        response = self.client.post(self.calendar_url, self.help_data(
            name="  山田 太郎（他店ヘルプ）  ",
            store=self.other_store.pk, user=self.staff.pk,
            membership=self.membership.pk, is_generated="on",
        ))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        help_shift = HelpShift.objects.get()
        self.assertEqual(help_shift.store, self.store)
        self.assertEqual(help_shift.name, "山田 太郎（他店ヘルプ）")
        self.assertEqual(help_shift.work_date, self.work_date)
        self.assertEqual((help_shift.start_time, help_shift.end_time), (time(9), time(18)))
        self.assertEqual(help_shift.note, "東店からヘルプ、レジ担当")
        self.assertEqual(help_shift.display_color, "orange")
        self.assertFalse(help_shift.is_generated)
        self.assertEqual(help_shift.display_color_class, "shift-color-orange")
        after = self.snapshots()
        self.assertEqual(after["users"], before["users"])
        self.assertEqual(after["memberships"], before["memberships"])
        self.assertEqual(after["shifts"], before["shifts"])

    def test_optional_note_and_color_may_be_blank(self):
        response = self.client.post(self.calendar_url, self.help_data(note="", display_color=""))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        help_shift = HelpShift.objects.get()
        self.assertEqual(help_shift.note, "")
        self.assertEqual(help_shift.display_color, "")
        self.assertEqual(help_shift.display_color_class, "shift-color-blue")

    def test_creation_cannot_submit_a_different_day_than_selected_calendar_day(self):
        before = self.snapshots()

        response = self.client.post(self.calendar_url, self.help_data(work_date="2030-11-13"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["help_form"].errors.get("work_date"))
        self.assertEqual(self.snapshots(), before)

    def test_maximum_name_and_note_lengths_are_saved(self):
        response = self.client.post(self.calendar_url, self.help_data(name="名" * 150, note="あ" * 500))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        help_shift = HelpShift.objects.get()
        self.assertEqual(help_shift.name, "名" * 150)
        self.assertEqual(help_shift.note, "あ" * 500)

    def test_invalid_help_input_does_not_create_any_shift(self):
        cases = (
            ("name", ""), ("name", "   "), ("name", "名" * 151),
            ("note", "あ" * 501), ("display_color", "yellow"),
            ("work_date", "0001-01-01"), ("work_date", "9999-12-31"),
            ("work_date", "2030-02-30"), ("start_time", "24:00"),
            ("end_time", "09:00"), ("end_time", "08:59"),
        )
        before = self.snapshots()
        for field, value in cases:
            with self.subTest(field=field, value=value):
                response = self.client.post(self.calendar_url, self.help_data(**{field: value}))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["help_form"].errors.get(field))
                self.assertFalse(response.context["form"].is_bound)
                self.assertFalse(response.context["outside_form"].is_bound)
                self.assertEqual(self.snapshots(), before)

    def test_same_help_name_overlapping_time_is_rejected_but_adjacent_time_is_allowed(self):
        self.make_help(start_time=time(9), end_time=time(12))
        before = self.snapshots()
        for start, end in (("08:00", "10:00"), ("10:00", "11:00"), ("11:00", "13:00")):
            with self.subTest(start=start, end=end):
                response = self.client.post(self.calendar_url, self.help_data(start_time=start, end_time=end))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["help_form"].non_field_errors())
                self.assertEqual(self.snapshots(), before)

        response = self.client.post(self.calendar_url, self.help_data(start_time="12:00", end_time="14:00"))
        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.assertEqual(HelpShift.objects.count(), 2)

    def test_overlap_validation_is_scoped_to_name_day_and_store(self):
        self.make_help(store=self.other_store)
        self.make_help(work_date=date(2030, 11, 13))
        self.make_help(name="別のヘルプ")

        response = self.client.post(self.calendar_url, self.help_data())

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.assertEqual(HelpShift.objects.count(), 4)

    def test_help_edit_loads_existing_values_and_updates_without_changing_store(self):
        help_shift = self.make_help()
        response = self.client.get(self.edit_url(help_shift))
        self.assertEqual(response.status_code, 200)
        for field in ("name", "work_date", "start_time", "end_time", "note", "display_color"):
            self.assertEqual(response.context["form"][field].value(), getattr(help_shift, field))

        next_date = date(2030, 12, 1)
        response = self.client.post(self.edit_url(help_shift), self.help_data(
            prefixed=False, name="鈴木 花子（ヘルプ）", work_date=next_date.isoformat(),
            start_time="10:30", end_time="16:15", note="変更したヘルプ先", display_color="pink",
            store=self.other_store.pk, user=self.staff.pk,
        ))
        self.assertRedirects(response, self.calendar_for(next_date), fetch_redirect_response=False)
        help_shift.refresh_from_db()
        self.assertEqual(help_shift.store, self.store)
        self.assertEqual(help_shift.name, "鈴木 花子（ヘルプ）")
        self.assertEqual(help_shift.work_date, next_date)
        self.assertEqual((help_shift.start_time, help_shift.end_time), (time(10, 30), time(16, 15)))
        self.assertEqual(help_shift.note, "変更したヘルプ先")
        self.assertEqual(help_shift.display_color, "pink")

    def test_edit_does_not_conflict_with_itself_but_does_conflict_with_other_help_shift(self):
        help_shift = self.make_help(start_time=time(9), end_time=time(12))
        response = self.client.post(self.edit_url(help_shift), self.help_data(
            prefixed=False, start_time="09:00", end_time="12:00",
        ))
        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.make_help(start_time=time(13), end_time=time(15))
        before = self.snapshots()

        response = self.client.post(self.edit_url(help_shift), self.help_data(
            prefixed=False, start_time="11:00", end_time="14:00", note="保存されない",
        ))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(self.snapshots(), before)

    def test_invalid_edit_preserves_existing_help_record(self):
        help_shift = self.make_help()
        before = self.snapshots()
        for field, value in (("name", " "), ("note", "あ" * 501), ("end_time", "08:00"), ("display_color", "yellow")):
            with self.subTest(field=field):
                response = self.client.post(self.edit_url(help_shift), self.help_data(
                    prefixed=False, **{field: value},
                ))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors.get(field))
                self.assertEqual(self.snapshots(), before)

    def test_delete_get_confirms_and_only_post_deletes(self):
        help_shift = self.make_help()
        response = self.client.get(self.delete_url(help_shift))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, help_shift.name)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertTrue(HelpShift.objects.filter(pk=help_shift.pk).exists())

        response = self.client.post(self.delete_url(help_shift))
        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.assertFalse(HelpShift.objects.filter(pk=help_shift.pk).exists())

    def test_other_store_help_shift_cannot_be_edited_or_deleted(self):
        foreign_help = self.make_help(store=self.other_store)
        before = self.snapshots()
        for url in (self.edit_url(foreign_help), self.delete_url(foreign_help)):
            for method in (self.client.get, self.client.post):
                with self.subTest(url=url, method=method.__name__):
                    self.assertEqual(method(url, self.help_data(prefixed=False)).status_code, 404)
                    self.assertEqual(self.snapshots(), before)

    def test_nonmanager_cannot_add_edit_or_delete_help_shifts(self):
        help_shift = self.make_help()
        self.client.force_login(self.staff)
        before = self.snapshots()
        for url, data in (
            (self.calendar_url, self.help_data()),
            (self.edit_url(help_shift), self.help_data(prefixed=False)),
            (self.delete_url(help_shift), {}),
        ):
            with self.subTest(url=url):
                response = self.client.post(url, data)
                self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
                self.assertEqual(self.snapshots(), before)

    def test_inactive_manager_cannot_change_help_shifts(self):
        help_shift = self.make_help()
        self.manager_membership.is_active = False
        self.manager_membership.save(update_fields=["is_active"])
        before = self.snapshots()
        for url in (self.calendar_url, self.edit_url(help_shift), self.delete_url(help_shift)):
            with self.subTest(url=url):
                response = self.client.post(url, self.help_data(prefixed=False))
                self.assertRedirects(response, reverse("manager_dashboard"), fetch_redirect_response=False)
                self.assertEqual(self.snapshots(), before)

    def test_anonymous_help_edit_and_delete_redirect_to_login(self):
        help_shift = self.make_help()
        self.client.logout()
        for url in (self.edit_url(help_shift), self.delete_url(help_shift)):
            with self.subTest(url=url):
                response = self.client.post(url, {})
                self.assertRedirects(response, f"{reverse('login')}?next={url}", fetch_redirect_response=False)
        self.assertTrue(HelpShift.objects.filter(pk=help_shift.pk).exists())

    def test_edit_and_delete_reject_unsupported_methods(self):
        help_shift = self.make_help()
        before = self.snapshots()
        for url in (self.edit_url(help_shift), self.delete_url(help_shift)):
            with self.subTest(url=url):
                self.assertEqual(self.client.put(url, {}).status_code, 405)
                self.assertEqual(self.snapshots(), before)

    def test_create_edit_and_delete_require_csrf_and_accept_valid_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        client.get(self.calendar_url)
        self.assertEqual(client.post(self.calendar_url, self.help_data()).status_code, 403)
        self.assertFalse(HelpShift.objects.exists())
        response = client.post(self.calendar_url, self.help_data(), HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        help_shift = HelpShift.objects.get()

        for url, data in ((self.edit_url(help_shift), self.help_data(prefixed=False, note="CSRFで保護")),
                          (self.delete_url(help_shift), {})):
            with self.subTest(url=url):
                before = self.snapshots()
                self.assertEqual(client.post(url, data).status_code, 403)
                self.assertEqual(self.snapshots(), before)
                response = client.post(url, data, HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
                self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        self.assertFalse(HelpShift.objects.exists())

    def test_calendar_selected_day_and_month_status_include_help_shifts_only_in_this_store(self):
        help_shift = self.make_help()
        foreign_help = self.make_help(store=self.other_store, name="他店舗だけの名前")
        next_month_help = self.make_help(work_date=date(2030, 12, 1), name="翌月だけの名前")

        response = self.client.get(self.calendar_url)

        self.assertEqual(list(response.context["help_shifts"]), [help_shift])
        self.assertContains(response, help_shift.name)
        self.assertContains(response, help_shift.note)
        self.assertNotContains(response, foreign_help.name)
        self.assertNotContains(response, next_month_help.name)
        selected = next(day for week in response.context["calendar_weeks"] for day in week if day["date"] == self.work_date)
        self.assertTrue(selected["has_shift"])

    def test_manager_and_employee_table_show_grouped_help_rows_with_times_notes_and_color(self):
        help_shift = self.make_help(end_time=time(12, 30), note="<script>alert('help')</script>")
        second = self.make_help(start_time=time(13), end_time=time(16))
        tomorrow = self.make_help(work_date=date(2030, 11, 13), start_time=time(9), end_time=time(10, 15))
        self.make_help(store=self.other_store, name="他店にだけいるヘルプ")
        self.make_help(work_date=date(2030, 12, 1), name="翌月だけのヘルプ")

        for user, name in ((self.manager, "manager_shift_overview"), (self.staff, "store_shift_overview")):
            with self.subTest(view=name):
                self.client.force_login(user)
                response = self.client.get(reverse(name), self.month_query)
                self.assertEqual(response.status_code, 200)
                rows = response.context["help_rows"]
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertEqual(row["name"], help_shift.name)
                self.assertEqual(len(row["cells"]), 30)
                self.assertEqual(row["working_days"], 2)
                self.assertEqual(row["shift_count"], 3)
                self.assertEqual(row["hours_display"], "7:45")
                day_cell = next(cell for cell in row["cells"] if cell["date"] == self.work_date)
                self.assertEqual(day_cell["shifts"], [help_shift, second])
                next_cell = next(cell for cell in row["cells"] if cell["date"] == tomorrow.work_date)
                self.assertEqual(next_cell["shifts"], [tomorrow])
                self.assertContains(response, help_shift.name)
                self.assertContains(response, escape(help_shift.note))
                self.assertNotContains(response, help_shift.note)
                self.assertContains(response, "shift-color-orange")
                self.assertNotContains(response, "他店にだけいるヘルプ")
                self.assertNotContains(response, "翌月だけのヘルプ")

    def test_help_people_count_once_per_day_but_multiple_shifts_add_to_totals(self):
        self.make_help(start_time=time(9), end_time=time(12))
        self.make_help(start_time=time(13), end_time=time(16))
        self.make_help(name="二人目のヘルプ", start_time=time(9), end_time=time(10))
        Shift.objects.create(
            user=self.staff, membership=self.membership, store=self.store,
            work_date=self.work_date, start_time=time(9), end_time=time(17),
        )

        response = self.client.get(reverse("manager_shift_overview"), self.month_query)

        self.assertEqual(response.context["total_shifts"], 4)
        self.assertEqual(response.context["scheduled_staff_count"], 1)
        self.assertEqual(response.context["scheduled_help_count"], 2)
        self.assertEqual(response.context["scheduled_people_count"], 3)
        self.assertEqual(response.context["total_help_shifts"], 3)
        self.assertEqual(response.context["total_working_days"], 3)
        self.assertEqual(response.context["total_hours_display"], "15:00")
        self.assertEqual(response.context["day_staff_counts"][self.work_date.day - 1], 3)

    def test_hidden_staff_remains_hidden_while_help_rows_remain_visible(self):
        self.membership.show_in_shift_overview = False
        self.membership.save(update_fields=["show_in_shift_overview"])
        Shift.objects.create(
            user=self.staff, membership=self.membership, store=self.store,
            work_date=self.work_date, start_time=time(9), end_time=time(17),
        )
        help_shift = self.make_help()
        response = self.client.get(reverse("manager_shift_overview"), self.month_query)

        self.assertNotIn(self.staff.pk, {row["user"].pk for row in response.context["rows"]})
        self.assertEqual([row["name"] for row in response.context["help_rows"]], [help_shift.name])
        self.assertEqual(response.context["total_shifts"], 1)
        self.assertTrue(Shift.objects.filter(user=self.staff).exists())

    def test_employee_without_view_permission_cannot_see_help_names_or_notes_in_table(self):
        help_shift = self.make_help()
        self.membership.can_view_shifts = False
        self.membership.save(update_fields=["can_view_shifts"])
        self.client.force_login(self.staff)

        response = self.client.get(reverse("store_shift_overview"), self.month_query)

        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, help_shift.name, status_code=403)
        self.assertNotContains(response, help_shift.note, status_code=403)

    def test_personal_shifts_do_not_include_help_people_or_their_notes(self):
        help_shift = self.make_help()
        self.client.force_login(self.staff)

        response = self.client.get(reverse("shift_list"), self.month_query)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, help_shift.name)
        self.assertNotContains(response, help_shift.note)

    def test_company_permissions_feature_remains_removed(self):
        self.make_help()
        for path in ("/manager/shifts/viewers/", "/manager/shifts/viewers/grant/", "/viewer/shifts/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
