from datetime import date, time

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils.html import escape

from accounts.models import Company, Store, StoreMembership
from scheduler.services import generate_shifts_for_store
from shifts.models import Availability, Requirement, Shift


class ShiftDetailsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        company = Company.objects.create(name="テスト会社")
        cls.store = Store.objects.create(company=company, name="本店")
        cls.other_store = Store.objects.create(company=company, name="別店舗")
        cls.manager = user_model.objects.create_user(
            username="details_manager", last_name="柴田", first_name="一翔"
        )
        cls.manager_membership = StoreMembership.objects.create(
            user=cls.manager, store=cls.store, role="manager", rank="A"
        )
        cls.staff = user_model.objects.create_user(
            username="details_staff", last_name="田中", first_name="花子"
        )
        cls.membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.store, role="staff", rank="B"
        )
        cls.second_membership = StoreMembership.objects.create(
            user=cls.staff, store=cls.other_store, role="staff", rank="C"
        )
        cls.alternate_staff = user_model.objects.create_user(
            username="details_alternate", last_name="鈴木", first_name="太郎"
        )
        cls.alternate_membership = StoreMembership.objects.create(
            user=cls.alternate_staff, store=cls.store, role="staff"
        )
        cls.foreign_staff = user_model.objects.create_user(
            username="details_foreign", last_name="佐藤", first_name="美咲"
        )
        cls.foreign_membership = StoreMembership.objects.create(
            user=cls.foreign_staff, store=cls.other_store, role="staff"
        )
        cls.work_date = date(2026, 11, 12)
        Availability.objects.create(
            user=cls.staff, membership=cls.membership, work_date=cls.work_date,
            start_time=time(8), end_time=time(18),
        )

    def setUp(self):
        self.client.force_login(self.manager)
        self.calendar_url = self.calendar_for(self.work_date)
        self.month_query = {"year": self.work_date.year, "month": self.work_date.month}

    def calendar_for(self, work_date):
        return (
            f"{reverse('manager_shift_list')}?year={work_date.year}"
            f"&month={work_date.month}&date={work_date.isoformat()}"
        )

    def make_shift(self, membership=None, **overrides):
        membership = membership or self.membership
        data = {
            "user": membership.user,
            "membership": membership,
            "store": membership.store,
            "work_date": self.work_date,
            "start_time": time(9),
            "end_time": time(10),
            "is_generated": False,
        }
        data.update(overrides)
        return Shift.objects.create(**data)

    def post_data(self, **overrides):
        data = {
            "membership": self.membership.pk,
            "work_date": self.work_date.isoformat(),
            "start_time": "09:00",
            "end_time": "10:00",
        }
        data.update(overrides)
        return data

    def edit_url(self, shift):
        return reverse("manager_shift_edit", args=[shift.pk])

    def test_model_defaults_and_color_classes_only_use_allowed_values(self):
        shift = Shift()
        self.assertEqual(shift.note, "")
        self.assertEqual(shift.display_color, "")
        self.assertEqual(shift.display_color_class, "shift-color-purple")
        shift.is_generated = False
        self.assertEqual(shift.display_color_class, "shift-color-blue")
        for color in ["blue", "green", "orange", "purple", "pink", "gray"]:
            with self.subTest(color=color):
                shift.display_color = color
                self.assertEqual(shift.display_color_class, f"shift-color-{color}")
        for generated in [True, False]:
            for invalid_color in ["red", 'blue" onclick="alert(1)', "BLUE"]:
                with self.subTest(generated=generated, color=invalid_color):
                    shift.is_generated = generated
                    shift.display_color = invalid_color
                    expected = "purple" if generated else "blue"
                    self.assertEqual(shift.display_color_class, f"shift-color-{expected}")

    def test_manual_creation_saves_maximum_length_note_and_color_in_managers_store(self):
        note = "朝の引き継ぎ\n" + "あ" * (500 - len("朝の引き継ぎ\n"))
        response = self.client.post(self.calendar_url, self.post_data(
            note=note, display_color="green", store=self.other_store.pk,
            user=self.foreign_staff.pk, is_generated="on",
        ))

        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        shift = Shift.objects.get()
        self.assertEqual(shift.note, note)
        self.assertEqual(shift.display_color, "green")
        self.assertEqual(shift.display_color_class, "shift-color-green")
        self.assertEqual(shift.store, self.store)
        self.assertEqual(shift.user, self.staff)
        self.assertEqual(shift.membership, self.membership)
        self.assertFalse(shift.is_generated)

    def test_creation_and_edit_forms_include_optional_details_and_existing_values(self):
        shift = self.make_shift(note="別店舗へヘルプ", display_color="orange")

        for url in [self.calendar_url, self.edit_url(shift)]:
            with self.subTest(url=url):
                response = self.client.get(url)
                form = response.context["form"]
                self.assertFalse(form.fields["note"].required)
                self.assertFalse(form.fields["display_color"].required)
                self.assertEqual(form.fields["note"].max_length, 500)
                self.assertEqual(
                    {value for value, label in form.fields["display_color"].choices},
                    {"", "blue", "green", "orange", "purple", "pink", "gray"},
                )
                self.assertContains(response, 'name="note"')
                self.assertContains(response, 'name="display_color"')
                if url == self.edit_url(shift):
                    self.assertEqual(form["note"].value(), shift.note)
                    self.assertEqual(form["display_color"].value(), shift.display_color)

    def test_edit_saves_each_color_and_note_and_changes_generated_shift_to_manual(self):
        shift = self.make_shift(is_generated=True)

        for color in ["blue", "green", "orange", "purple", "pink", "gray"]:
            with self.subTest(color=color):
                note = f"{color}のヘルプ先メモ"
                response = self.client.post(
                    self.edit_url(shift), self.post_data(note=note, display_color=color)
                )
                self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
                shift.refresh_from_db()
                self.assertEqual(shift.note, note)
                self.assertEqual(shift.display_color, color)
                self.assertFalse(shift.is_generated)
                self.assertEqual(shift.store, self.store)
                self.assertEqual(shift.user, self.staff)
                self.assertEqual(shift.membership, self.membership)

    def test_blank_details_and_legacy_posts_can_create_manual_shifts(self):
        for start_time, end_time, details in [
            ("09:00", "10:00", {"note": "", "display_color": ""}),
            ("11:00", "12:00", {}),
        ]:
            with self.subTest(details=details):
                response = self.client.post(self.calendar_url, self.post_data(
                    start_time=start_time, end_time=end_time, **details
                ))
                self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
                shift = Shift.objects.latest("pk")
                self.assertEqual(shift.note, "")
                self.assertEqual(shift.display_color, "")
                self.assertEqual(shift.display_color_class, "shift-color-blue")
                self.assertFalse(shift.is_generated)
        self.assertEqual(Shift.objects.count(), 2)

    def test_explicit_blanks_clear_details_and_legacy_edit_posts_keep_saved_values(self):
        shift = self.make_shift()

        for details, expected_note, expected_color, expected_class in [
            ({"note": "", "display_color": ""}, "", "", "shift-color-blue"),
            ({}, "元のメモ", "pink", "shift-color-pink"),
        ]:
            with self.subTest(details=details):
                Shift.objects.filter(pk=shift.pk).update(note="元のメモ", display_color="pink")
                response = self.client.post(self.edit_url(shift), self.post_data(**details))
                self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
                shift.refresh_from_db()
                self.assertEqual(shift.note, expected_note)
                self.assertEqual(shift.display_color, expected_color)
                self.assertEqual(shift.display_color_class, expected_class)

    def test_invalid_details_do_not_create_or_overwrite_saved_shifts(self):
        shift = self.make_shift(note="保存済みメモ", display_color="purple", is_generated=True)
        before = list(Shift.objects.order_by("pk").values())
        for url, start_time, end_time in [
            (self.calendar_url, "11:00", "12:00"),
            (self.edit_url(shift), "09:00", "10:00"),
        ]:
            for field, value in [
                ("note", "あ" * 501),
                ("display_color", "red"),
                ("display_color", 'pink" onclick="alert(1)'),
            ]:
                with self.subTest(url=url, field=field, value=value):
                    details = {"note": "変更候補", "display_color": "green", field: value}
                    response = self.client.post(url, self.post_data(
                        start_time=start_time, end_time=end_time, **details
                    ))
                    self.assertEqual(response.status_code, 200)
                    self.assertTrue(response.context["form"].errors.get(field))
                    self.assertEqual(list(Shift.objects.order_by("pk").values()), before)

    def test_invalid_times_do_not_save_changed_note_or_color(self):
        shift = self.make_shift(note="保存済みメモ", display_color="orange", is_generated=True)
        before = list(Shift.objects.values())
        for start_time, end_time in [("10:00", "09:00"), ("09:00", "09:00")]:
            with self.subTest(start=start_time, end=end_time):
                response = self.client.post(self.edit_url(shift), self.post_data(
                    start_time=start_time, end_time=end_time,
                    note="変更しないメモ", display_color="pink",
                ))
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context["form"].errors.get("end_time"))
                self.assertEqual(list(Shift.objects.values()), before)

    def test_foreign_membership_and_foreign_shift_cannot_be_changed_with_details(self):
        foreign_shift = self.make_shift(
            membership=self.foreign_membership, note="他店舗のメモ", display_color="gray"
        )
        before = list(Shift.objects.order_by("pk").values())
        response = self.client.post(self.calendar_url, self.post_data(
            membership=self.foreign_membership.pk, note="店舗外へ保存", display_color="pink"
        ))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("membership"))
        response = self.client.post(self.edit_url(foreign_shift), self.post_data(
            note="店舗外へ変更", display_color="pink"
        ))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(list(Shift.objects.order_by("pk").values()), before)

    def test_staff_cannot_create_or_edit_shift_details(self):
        shift = self.make_shift(note="変更不可", display_color="green")
        before = list(Shift.objects.values())
        self.client.force_login(self.staff)

        for url in [self.calendar_url, self.edit_url(shift)]:
            with self.subTest(url=url):
                response = self.client.post(url, self.post_data(note="権限外", display_color="pink"))
                self.assertRedirects(
                    response, reverse("manager_dashboard"), fetch_redirect_response=False
                )
                self.assertEqual(list(Shift.objects.values()), before)

    def test_saved_details_render_escaped_with_correct_user_and_store_scope(self):
        note = '<script>alert("shift-note")</script>引き継ぎメモ'
        response = self.client.post(self.calendar_url, self.post_data(note=note, display_color="pink"))
        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        own_shift = Shift.objects.get()
        self.make_shift(membership=self.second_membership, note="本人の別店舗メモ")
        self.make_shift(membership=self.alternate_membership, note="別の従業員のメモ")
        self.make_shift(membership=self.foreign_membership, note="別店舗の他人メモ")
        employee_calendar_url = (
            f"{reverse('availability_list')}?year={self.work_date.year}"
            f"&month={self.work_date.month}&date={self.work_date.isoformat()}"
        )

        for user, url, query, is_manager in [
            (self.manager, self.calendar_url, {}, True),
            (self.manager, reverse("manager_shift_overview"), self.month_query, True),
            (self.staff, reverse("shift_list"), self.month_query, False),
            (self.staff, employee_calendar_url, {}, False),
        ]:
            with self.subTest(user=user.pk, url=url):
                self.client.force_login(user)
                response = self.client.get(url, query)
                self.assertContains(response, escape(note))
                self.assertNotContains(response, note)
                self.assertContains(response, "shift-color-pink")
                self.assertNotContains(response, "別店舗の他人メモ")
                if is_manager:
                    self.assertNotContains(response, "本人の別店舗メモ")
                    self.assertContains(response, "別の従業員のメモ")
                else:
                    self.assertContains(response, "本人の別店舗メモ")
                    self.assertNotContains(response, "別の従業員のメモ")
        own_shift.refresh_from_db()
        self.assertEqual(own_shift.note, note)
        self.assertEqual(own_shift.display_color, "pink")

    def test_generation_defaults_and_edited_details_survive_regeneration(self):
        Requirement.objects.create(
            store=self.store, work_date=self.work_date, start_time=time(9),
            end_time=time(10), required_staff_count=1,
        )
        result = generate_shifts_for_store(self.store.pk, self.work_date)
        self.assertEqual(result["created_count"], 1)
        shift = Shift.objects.get()
        self.assertTrue(shift.is_generated)
        self.assertEqual(shift.note, "")
        self.assertEqual(shift.display_color, "")
        self.assertEqual(shift.display_color_class, "shift-color-purple")

        response = self.client.post(self.edit_url(shift), self.post_data(
            note="再生成しても残すヘルプ先", display_color="orange"
        ))
        self.assertRedirects(response, self.calendar_url, fetch_redirect_response=False)
        shift.refresh_from_db()
        self.assertFalse(shift.is_generated)
        before = list(Shift.objects.values())

        result = generate_shifts_for_store(self.store.pk, self.work_date)

        self.assertEqual(result, {"created_count": 0, "shortfall_count": 0, "requirement_count": 1})
        self.assertEqual(list(Shift.objects.values()), before)
