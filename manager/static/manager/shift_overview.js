const monthForm = document.querySelector(".overview-month-form");

if (monthForm) {
  monthForm.addEventListener("change", (event) => {
    if (["year", "month"].includes(event.target.name) && monthForm.checkValidity()) {
      monthForm.requestSubmit();
    }
  });
}
