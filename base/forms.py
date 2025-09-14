from typing import Optional

import background_task
from django import forms
from django.contrib.auth import forms as auth_forms
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.http import Http404

from base.views_utils import ClassAuth, HerdAuth, auth_herd

from . import models
from .templatetags.animal_filters import filter_text_to_default
from .traitsets import TRAITSET_CHOICES, Traitset


class EmailAuthenticationForm(auth_forms.AuthenticationForm):
    """A form to authenticate users using email instead of username."""

    username = forms.EmailField(
        required=True,
        label="Email",
        widget=forms.EmailInput(attrs={"autofocus": True}),
    )

    def clean(self):
        ERROR_MSG = forms.ValidationError(
            "Please enter a correct username and email. Note that both fields may be case-sensitive."
        )

        email = self.cleaned_data.get("username")
        user = User.objects.filter(email=email).order_by("id").last()
        if user:
            self.user_cache = authenticate(
                self.request,
                username=user.username,
                password=self.cleaned_data.get("password"),
            )
            if self.user_cache is None:
                raise ERROR_MSG
        else:
            raise ERROR_MSG

        return self.cleaned_data


class UserCreationForm(auth_forms.UserCreationForm):
    "A form to create new users."

    class Meta:
        model = User
        fields = [
            "username",
            "email",
            "first_name",
            "last_name",
            "password1",
            "password2",
        ]

    def clean_email(self) -> str:
        email = self.cleaned_data["email"]
        if User.objects.filter(email=email).count() > 0:
            raise forms.ValidationError(
                "A user with that email already exists."
            )

        return email


class CreateClassForm(forms.ModelForm):
    "A form to create a new class (base.models.Class)."

    traitset = forms.ChoiceField(choices=TRAITSET_CHOICES)
    initial_males = forms.IntegerField(min_value=1, max_value=100)
    initial_females = forms.IntegerField(min_value=1, max_value=25)

    class Meta:
        model = models.Class
        fields = ["name", "info", "traitset"]

    def clean_name(self):
        name = self.data["name"]
        error = forms.ValidationError("Name cannot contain < or >")
        if "<" in name or ">" in name:
            raise error

        return name

    def save(self, user: User) -> models.Class:
        return models.Class.create_new(
            user,
            name=self.cleaned_data["name"],
            info=self.cleaned_data["info"],
            traitsetname=self.cleaned_data["traitset"],
            initial_males=self.cleaned_data["initial_males"],
            initial_females=self.cleaned_data["initial_females"],
        )


class UpdateClassForm(forms.ModelForm):
    "A form to update a class (base.models.Class)."

    traitset = forms.CharField(disabled=True)
    classcode = forms.CharField(disabled=True)
    enrollment_tokens = forms.IntegerField(disabled=True)
    quarantine_days = forms.IntegerField(min_value=0, max_value=60)

    class Meta:
        model = models.Class
        fields = [
            "name",
            "traitset",
            "classcode",
            "enrollment_tokens",
            "info",
            "default_animal",
            "quarantine_days",
            "allow_other_animals",
            "allow_herd_rename",
            "net_merit_visibility",
            "hide_female_pta",
        ]

    def clean_name(self):
        name = self.data["name"]
        error = forms.ValidationError("Name cannot contain < or >")
        if "<" in name or ">" in name:
            raise error

        return name

    def __init__(self, connectedclass: models.Class, *args, **kwargs):
        super(UpdateClassForm, self).__init__(*args, **kwargs)
        traitset = Traitset(self.instance.traitset)

        trait_visibility_choices = [
            (x.uid, filter_text_to_default(f"<{x.uid}>", connectedclass))
            for x in traitset.traits
        ]

        recessive_visibility_choices = [
            (x.uid, filter_text_to_default(f"<{x.uid}>", connectedclass))
            for x in traitset.recessives
        ]

        self.fields["genotype_visibility"] = forms.MultipleChoiceField(
            required=False,
            choices=trait_visibility_choices,
            widget=forms.CheckboxSelectMultiple(),
            initial=[
                x
                for x, _ in trait_visibility_choices
                if self.instance.trait_visibility[x][0]
            ],
        )

        self.fields["phenotype_visibility"] = forms.MultipleChoiceField(
            required=False,
            choices=trait_visibility_choices,
            widget=forms.CheckboxSelectMultiple(),
            initial=[
                x
                for x, _ in trait_visibility_choices
                if self.instance.trait_visibility[x][1]
            ],
        )

        self.fields["pta_visibility"] = forms.MultipleChoiceField(
            required=False,
            choices=trait_visibility_choices,
            widget=forms.CheckboxSelectMultiple(),
            initial=[
                x
                for x, _ in trait_visibility_choices
                if self.instance.trait_visibility[x][2]
            ],
        )

        self.fields["recessive_visibility"] = forms.MultipleChoiceField(
            required=False,
            choices=recessive_visibility_choices,
            widget=forms.CheckboxSelectMultiple(),
            initial=[
                x
                for x, _ in recessive_visibility_choices
                if self.instance.recessive_visibility[x]
            ],
        )

        self.fields["default_animal"] = forms.ChoiceField(
            choices=traitset.animal_choices
        )

    def save(self) -> None:
        self.instance.info = self.cleaned_data["info"]

        for trait in self.instance.trait_visibility:
            self.instance.trait_visibility[trait] = [
                trait in self.cleaned_data["genotype_visibility"],
                trait in self.cleaned_data["phenotype_visibility"],
                trait in self.cleaned_data["pta_visibility"],
            ]

        for recessive in self.instance.recessive_visibility:
            self.instance.recessive_visibility[recessive] = (
                recessive in self.cleaned_data["recessive_visibility"]
            )

        if self.instance.allow_other_animals is False:
            enrollments = models.Enrollment.objects.filter(
                connectedclass=self.instance
            )
            for enrollment in enrollments:
                enrollment.animal = self.instance.default_animal

            models.Enrollment.objects.bulk_update(enrollments, ["animal"])

        self.instance.save()


class ClassReadonlyForm(forms.ModelForm):
    "A form for the students view of the class (base.models.Class)."

    name = forms.CharField(disabled=True)
    traitset = forms.CharField(disabled=True)
    info = forms.CharField(disabled=True, widget=forms.Textarea)

    class Meta:
        model = models.Class
        fields = ["name", "traitset", "info"]


class JoinClass(forms.Form):
    "A form to enroll in a class (base.models.Class)."

    classcode = forms.CharField()

    def __init__(self, user: User = None, *args, **kwargs):
        self.user = user
        super(JoinClass, self).__init__(*args, **kwargs)

    def clean_classcode(self):
        try:
            connectedclass = models.Class.objects.get(
                classcode=self.cleaned_data["classcode"]
            )
        except models.Class.DoesNotExist:
            raise forms.ValidationError("No class with this code was found.")

        if models.Enrollment.objects.filter(
            connectedclass=connectedclass, student=self.user
        ).exists():
            raise forms.ValidationError("Already enrolled in class.")

        if models.EnrollmentRequest.objects.filter(
            connectedclass=connectedclass, student=self.user
        ).exists():
            raise forms.ValidationError(
                "Already requested enrollment in class."
            )

        if connectedclass.teacher == self.user:
            raise forms.ValidationError("Cannot enroll in class as teacher.")

        return self.cleaned_data["classcode"]

    def save(self) -> models.EnrollmentRequest:
        connectedclass = models.Class.objects.get(
            classcode=self.cleaned_data["classcode"]
        )
        return models.EnrollmentRequest.create_new(self.user, connectedclass)


class RequestTokens(forms.Form):
    "A form to request more enrollment tokens"

    institution = forms.CharField(max_length=255)
    tokens_needed = forms.IntegerField(min_value=1, max_value=999)


class BreedHerd(forms.Form):
    "A form to breed herds."

    class ValidationCatch:
        males: list[models.Animal]
        assignment: models.Assignment
        assignment_fulfillment: models.AssignmentFulfillment
        assignment_step: models.AssignmentStep

    males = forms.JSONField(
        widget=forms.TextInput(
            attrs={"class": "full-width", "disabled": True}
        ),
        label="",
        initial=list,
    )
    assignment = forms.IntegerField(widget=forms.HiddenInput)
    validation_catch: Optional[ValidationCatch] = None

    def validate_males(self, class_auth: ClassAuth.Student) -> bool:
        males = self.cleaned_data["males"]

        if type(males) != list:
            return False

        self.validation_catch.males = []
        for male in males:
            try:
                animal = models.Animal.objects.get(id=male)
                self.validation_catch.males.append(animal)
            except models.Animal.DoesNotExist:
                return False

            if not animal.male:
                return False

            try:
                _herd_auth = auth_herd(class_auth, animal.herd_id)
            except Http404:
                return False

        return True

    def validate_assignment(self, class_auth: ClassAuth.Student) -> bool:
        assignment = self.cleaned_data["assignment"]

        try:
            assignment = models.Assignment.objects.get(id=assignment)
            self.validation_catch.assignment = assignment
        except models.Assignment.DoesNotExist:
            return False

        try:
            assignment_fulfillment = models.AssignmentFulfillment.objects.get(
                assignment=assignment, enrollment=class_auth.enrollment
            )
            self.validation_catch.assignment_fulfillment = (
                assignment_fulfillment
            )
        except models.AssignmentFulfillment.DoesNotExist:
            return False

        try:
            assignment_step = models.AssignmentStep.objects.get(
                assignment=assignment,
                number=assignment_fulfillment.current_step,
            )
            self.validation_catch.assignment_step = assignment_step
        except models.AssignmentStep.DoesNotExist:
            return False

        return assignment_step.step == models.AssignmentStep.CHOICE_BREED

    def is_valid(self, class_auth: ClassAuth.Student) -> bool:
        self.validation_catch = self.ValidationCatch()

        if super().is_valid() is False:
            return False

        if self.validate_males(class_auth) is False:
            return False

        return self.validate_assignment(class_auth)

    def save(
        self, herd_auth: HerdAuth.EnrollmentHerd
    ) -> models.Herd.BreedingResults:
        assignment = self.validation_catch.assignment
        assignment = f"{assignment.id}: {assignment.name}"
        max_len = models.Animal._meta.get_field("assignment").max_length

        if len(assignment) > max_len:
            assignment = assignment[:max_len]

        breeding_result = herd_auth.herd.breed_herd(
            self.validation_catch.males, assignment
        )
        self.validation_catch.assignment_fulfillment.current_step += 1
        self.validation_catch.assignment_fulfillment.save()
        return breeding_result


class SubmitAnimal(forms.Form):
    "A form for animal submissions."

    class ValidationCatch:
        assignment: models.Assignment
        assignment_fulfillment: models.AssignmentFulfillment
        assignment_step: models.AssignmentStep

    assignment = forms.IntegerField(widget=forms.HiddenInput)
    validation_catch: Optional[ValidationCatch] = None

    def is_valid(self, class_auth: ClassAuth.Student) -> bool:
        self.validation_catch = self.ValidationCatch()

        if super().is_valid() is False:
            return False

        assignment = self.cleaned_data["assignment"]

        try:
            assignment = models.Assignment.objects.get(id=assignment)
            self.validation_catch.assignment = assignment
        except models.Assignment.DoesNotExist:
            return False

        try:
            assignment_fulfillment = models.AssignmentFulfillment.objects.get(
                assignment=assignment, enrollment=class_auth.enrollment
            )
            self.validation_catch.assignment_fulfillment = (
                assignment_fulfillment
            )
        except models.AssignmentFulfillment.DoesNotExist:
            return False

        try:
            assignment_step = models.AssignmentStep.objects.get(
                assignment=assignment,
                number=assignment_fulfillment.current_step,
            )
            self.validation_catch.assignment_step = assignment_step
        except models.AssignmentStep.DoesNotExist:
            return False

        return assignment_step.step in [
            models.AssignmentStep.CHOICE_MALE_SUBMISSION,
            models.AssignmentStep.CHOICE_FEMALE_SUBMISSION,
        ]

    @background_task.background(schedule=0)
    @staticmethod
    def move_animal(animal_id: int):
        animal = (
            models.Animal.objects.select_related("connectedclass")
            .defer("pedigree", "connectedclass__trend_log")
            .get(id=animal_id)
        )
        animal.herd = animal.connectedclass.class_herd
        animal.save()

    def save(
        self, class_auth: ClassAuth.Student, animal: models.Animal
    ) -> None:
        animal.herd = None
        animal.save()

        day = 60 * 60 * 24
        self.move_animal(
            animal_id=animal.id,
            schedule=class_auth.connectedclass.quarantine_days * day,
        )

        self.validation_catch.assignment_fulfillment.current_step += 1
        self.validation_catch.assignment_fulfillment.save()


class NewAssignment(forms.ModelForm):
    "A form to create new assignments."

    startdate = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    duedate = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    steps = forms.JSONField(widget=forms.HiddenInput, required=False)

    class Meta:
        fields = ["name", "startdate", "duedate"]
        model = models.Assignment

    def clean_steps(self) -> list[str]:
        steps = self.cleaned_data["steps"]
        possible_steps = [x[0] for x in models.AssignmentStep.CHOICES]

        if type(steps) is not list:
            raise forms.ValidationError("Invalid steps")

        for step in steps:
            if step not in possible_steps:
                raise forms.ValidationError("Invalid step")

        return steps

    def clean_name(self):
        name = self.data["name"]
        error = forms.ValidationError("Name cannot contain < or >")
        if "<" in name or ">" in name:
            raise error

        return name

    def save(self, connectedclass: models.Class) -> models.Assignment:
        return models.Assignment.create_new(
            self.cleaned_data["name"],
            self.cleaned_data["startdate"],
            self.cleaned_data["duedate"],
            self.cleaned_data["steps"],
            connectedclass,
        )


class UpdateAssignment(forms.ModelForm):
    "A form to update assignments."

    startdate = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    duedate = forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    steps = forms.JSONField(
        disabled=True, widget=forms.TextInput, required=False
    )

    def __init__(self, *args, **kwargs):
        super(UpdateAssignment, self).__init__(*args, **kwargs)

        self.fields["steps"].initial = [
            x.step
            for x in models.AssignmentStep.objects.filter(
                assignment=self.instance
            )
        ]

    class Meta:
        fields = ["name", "startdate", "duedate"]
        model = models.Assignment


class UpdateEnrollmentForm(forms.ModelForm):
    "A form to update an enrollment."

    team_name = forms.CharField()  # Overwritten Later

    def __init__(self, *args, **kwargs):
        super(UpdateEnrollmentForm, self).__init__(*args, **kwargs)

        self.fields["animal"] = forms.ChoiceField(
            label="Animal Filter",
            choices=Traitset(
                self.instance.connectedclass.traitset
            ).animal_choices,
            disabled=not self.instance.connectedclass.allow_other_animals,
        )

        self.fields["team_name"] = forms.CharField(
            max_length=200,
            label="Team Name",
            disabled=not self.instance.connectedclass.allow_herd_rename,
            initial=self.instance.herd.name.removesuffix(
                "'s <herd>"
            ).removesuffix("' <herd>"),
        )

    def save(self, *args, **kwargs):
        super(UpdateEnrollmentForm, self).save(*args, **kwargs)

        self.instance.herd.name = (
            models.Enrollment.generate_herd_from_team_name(
                self.cleaned_data["team_name"]
            )
        )

        self.instance.herd.save()

    class Meta:
        model = models.Enrollment
        fields = ["animal"]


class Account(forms.ModelForm):
    "A form to update accounts."

    username = forms.CharField(disabled=True)
    email = forms.EmailField(disabled=True)
    first_name = forms.CharField()
    last_name = forms.CharField()

    class Meta:
        model = User
        fields = ["username", "email", "first_name", "last_name"]


class DeleteAccount(forms.Form):
    "A form to delete accounts."

    password = forms.CharField(strip=False, widget=forms.PasswordInput)

    def is_valid(self, user: User) -> bool:
        self.user = user
        return super().is_valid()

    def clean_password(self) -> str:
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError(
                "Password was incorrect.",
                code="password_incorrect",
            )
        return password
