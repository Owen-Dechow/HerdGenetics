import re
from sys import prefix
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.mail import mail_admins, mail_managers
from django.db import transaction
from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseRedirect,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, render
from django.utils.html import SafeString
from django.utils.timezone import now
from django.views.decorators.http import require_POST

from base.traitsets import (
    DOCUMENTED_FUNCS,
    Traitset,
    REGISTERED as registered_traitsets,
)

from . import forms
from . import models
from . import csv
from . import names as nms
from .templatetags.animal_filters import filter_text_to_default
from .views_utils import (
    ClassAuth,
    HerdAuth,
    auth_class,
    auth_herd,
    deleteclass_background,
)


#### AUTH PAGE VIEWS ####
@transaction.atomic
def signup(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = forms.UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return HttpResponseRedirect("/")

    else:
        form = forms.UserCreationForm()

    return render(request, "registration/register.html", {"form": form})


class EmailLoginView(LoginView):
    authentication_form = forms.EmailAuthenticationForm
    template = "registration/login.html"


@transaction.atomic
@login_required
def account(request: HttpRequest) -> HttpResponse | HttpResponseRedirect:
    delete_form = forms.DeleteAccount(prefix="delete")
    form = forms.Account(instance=request.user, prefix="normal")

    if request.method == "POST":
        if "delete-form" in request.POST:
            delete_form = forms.DeleteAccount(request.POST, prefix="delete")
            if delete_form.is_valid(request.user):
                request.user.delete()
                return HttpResponseRedirect("/")
        else:
            form = forms.Account(
                request.POST, prefix="normal", instance=request.user
            )
            if form.is_valid():
                form.save()
                return HttpResponseRedirect("/auth/account")

    return render(
        request,
        "registration/account.html",
        {
            "form": form,
            "delete_form": delete_form,
        },
    )


#### PAGE VIEWS ####
def homepage(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        owned_classes = models.Class.objects.select_related("teacher").filter(
            teacher=request.user, deleted=False
        )
        enrollments = models.Enrollment.objects.select_related(
            "connectedclass"
        ).filter(
            student=request.user,
            connectedclass__deleted=False,
        )
        classes = list(owned_classes) + [x.connectedclass for x in enrollments]
        classes = {x: x.get_open_assignments() for x in classes}

        requested_classes = [
            x.connectedclass
            for x in models.EnrollmentRequest.objects.select_related(
                "connectedclass"
            ).filter(student=request.user)
        ]
    else:
        classes = requested_classes = []

    return render(
        request,
        "base/homepage.html",
        {"classes": classes, "requested_classes": requested_classes},
    )


def about(request: HttpRequest) -> HttpResponse:
    return render(request, "base/about.html")


@login_required
@transaction.atomic
def createclass(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = forms.CreateClassForm(request.POST)
        if form.is_valid():
            connectedclass = form.save(request.user)
            return HttpResponseRedirect(f"/class/{connectedclass.id}")
    else:
        form = forms.CreateClassForm

    return render(request, "base/createclass.html", {"form": form})


@transaction.atomic
@login_required
def openclass(request: HttpRequest, classid: int) -> HttpResponse:
    class_auth = auth_class(request, classid, "class_herd")
    connectedclass = class_auth.connectedclass

    if type(class_auth) in ClassAuth.TEACHER_ADMIN:
        enrollment = None
        enrollment_form = None
        if request.method == "POST":
            form = forms.UpdateClassForm(
                class_auth.connectedclass,
                request.POST,
                instance=connectedclass,
            )
            if form.is_valid():
                form.save()
        else:
            form = forms.UpdateClassForm(
                class_auth.connectedclass, instance=connectedclass
            )
    elif type(class_auth) is ClassAuth.Student:
        enrollment = class_auth.enrollment
        form = forms.ClassReadonlyForm(instance=connectedclass)
        enrollment_form = forms.UpdateEnrollmentForm(instance=enrollment)
    else:
        form = None
        enrollment = None
        enrollment_form = None

    return render(
        request,
        "base/openclass.html",
        {
            "class": connectedclass,
            "form": form,
            "enrollment": enrollment,
            "enrollment_form": enrollment_form,
            "teacher_status": type(class_auth) in ClassAuth.TEACHER_ADMIN,
        },
    )


@login_required
def enrollments(request: HttpRequest, classid: int) -> HttpResponse:
    class_auth = auth_class(request, classid)
    connectedclass = class_auth.connectedclass

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to access enrollment page")

    return render(request, "base/enrollments.html", {"class": connectedclass})


@login_required
def request_tokens(request: HttpRequest, classid: int) -> HttpResponse:
    class_auth = auth_class(request, classid)
    connectedclass = class_auth.connectedclass

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to request tokens")

    if request.method == "POST":
        form = forms.RequestTokens(request.POST)
        if form.is_valid():
            institution = form.cleaned_data["institution"]
            needed = form.cleaned_data["tokens_needed"]

            msg = (
                "Request for enrollment tokens:\n"
                + f"Institution: {institution}\n"
                + f"Professor: {request.user.get_full_name()} ({request.user.email})\n"
                + f"Tokens Requested: {needed}\n"
                + f"Class ID: {connectedclass.id}\n"
                + f"Class Name: {connectedclass.name}\n"
                + f"Class Info: \n{connectedclass.info}\n"
            )

            mail_managers(f"Token Request: {institution}", msg)
        return HttpResponseRedirect(
            f"/class/{connectedclass.id}/enrollments/request-tokens/sent"
        )
    else:
        form = forms.RequestTokens

    return render(
        request,
        "base/request_enrollment_tokens.html",
        {"class": connectedclass, "form": form},
    )


def requested_tokens(request: HttpRequest, classid: int):
    class_auth = auth_class(request, classid)
    connectedclass = class_auth.connectedclass

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to request tokens")

    return render(
        request,
        "base/requested_enrollment_tokens.html",
        {"class": connectedclass},
    )


@transaction.atomic
@login_required
def assignments(
    request: HttpRequest, classid: int
) -> HttpResponse | HttpResponseRedirect:
    class_auth = auth_class(request, classid)
    connectedclass = class_auth.connectedclass

    if request.method == "POST":
        form = forms.NewAssignment(request.POST)
        if form.is_valid():
            assignment = form.save(connectedclass)
            return HttpResponseRedirect(
                f"/class/{classid}/assignments/{assignment.id}"
            )
    else:
        form = forms.NewAssignment

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to access assignments page")

    enrollments = models.Enrollment.objects.filter(
        connectedclass=connectedclass
    ).order_by("student__first_name", "student__last_name")
    assignments = [
        (
            a,
            models.AssignmentStep.objects.filter(assignment=a).count(),
            models.AssignmentFulfillment.objects.filter(assignment=a).order_by(
                "enrollment__student__first_name",
                "enrollment__student__last_name",
            ),
        )
        for a in models.Assignment.objects.filter(
            connectedclass=connectedclass
        ).order_by("duedate")
    ]

    return render(
        request,
        "base/assignments.html",
        {
            "class": connectedclass,
            "assignments": assignments,
            "current_date": now(),
            "enrollments": enrollments,
            "form": form,
        },
    )


@transaction.atomic
@login_required
def openassignment(
    request: HttpRequest, classid: int, assignmentid: int
) -> HttpResponse | HttpResponseRedirect:
    class_auth = auth_class(request, classid)
    assignment = get_object_or_404(models.Assignment, id=assignmentid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to access assignments page")

    if request.method == "POST":
        form = forms.UpdateAssignment(request.POST, instance=assignment)
        if form.is_valid():
            form.save()
            HttpResponseRedirect("")
    else:
        form = forms.UpdateAssignment(instance=assignment)

    return render(
        request,
        "base/openassignment.html",
        {
            "form": form,
            "assignment": assignment,
            "class": class_auth.connectedclass,
        },
    )


@transaction.atomic
@login_required
def joinclass(request: HttpRequest) -> HttpResponse | HttpResponseRedirect:
    if request.method == "POST":
        form = forms.JoinClass(request.user, request.POST)
        if form.is_valid():
            form.save()
            return HttpResponseRedirect(f"/class/requested")
    else:
        form = forms.JoinClass

    return render(request, "base/joinclass.html", {"form": form})


@login_required
def requestedclass(request: HttpRequest) -> HttpResponse:
    return render(request, "base/requestedclass.html", {})


@login_required
def openherd(request: HttpRequest, classid: int, herdid: int) -> HttpResponse:
    class_auth = auth_class(request, classid, "class_herd")
    herd_auth = auth_herd(class_auth, herdid, "enrollment")

    context = {}
    if type(class_auth) is ClassAuth.Student:
        context = {"enrollment": class_auth.enrollment}

    context["class"] = class_auth.connectedclass
    context["herd_auth"] = herd_auth
    context["collect_assignments"] = type(herd_auth) is HerdAuth.EnrollmentHerd
    context["breed_form"] = forms.BreedHerd

    return render(request, "base/openherd.html", context)


def equations(request: HttpRequest) -> HttpResponse:
    equations = {}
    for header, funcs in DOCUMENTED_FUNCS.items():
        equations[header] = []
        for func in funcs:
            doc_string = ""
            for line in func.__doc__.split("\n"):
                stripped = line.strip()
                if "#" in stripped:
                    html = stripped.replace("#", "<span>#", 1) + "</span>"
                else:
                    html = stripped

                doc_string += html + "<br>"

            sig = ""
            for line in func._sig.split("\n"):
                stripped = line.strip()
                if "#" in stripped:
                    html = stripped.replace("#", "<span>#", 1) + "</span>"
                else:
                    html = stripped

                sig += html + "<br>"

            equations[header].append((SafeString(sig), SafeString(doc_string)))

    return render(request, "base/equations.html", {"equations": equations})


def traitset_overview(request: HttpRequest, traitsetname: str) -> HttpResponse:
    try:
        traitset = Traitset(traitsetname)
    except FileNotFoundError as e:
        raise Http404(e)

    return render(
        request, "base/traitset_overview.html", {"traitset": traitset}
    )


def traitsets(request: HttpRequest) -> HttpResponse:
    traitsets = [(Traitset(x.name)) for x in registered_traitsets if x.enabled]
    deprecated_traitsets = [
        Traitset(x.name) for x in registered_traitsets if not x.enabled
    ]

    return render(
        request,
        "base/traitsets.html",
        context={
            "traitsets": traitsets,
            "deprecated_traitsets": deprecated_traitsets,
        },
    )


#### ACTION VIEWS ####
@require_POST
@transaction.atomic
@login_required
def update_enrollment(
    request: HttpRequest, classid: int
) -> HttpResponseRedirect:
    class_auth = auth_class(request, classid)

    if type(class_auth) is not ClassAuth.Student:
        raise Http404("Must be student to update filter")

    form = forms.UpdateEnrollmentForm(
        request.POST, instance=class_auth.enrollment
    )
    if form.is_valid():
        form.save()
    else:
        raise Http404("Invalid enrollment update request")

    return HttpResponseRedirect(f"/class/{classid}")


@transaction.atomic
@login_required
@require_POST
def delete_assignment(
    request: HttpRequest, classid: int, assignmentid: int
) -> HttpResponseRedirect:
    class_auth = auth_class(request, classid)
    assignment = get_object_or_404(models.Assignment, id=assignmentid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to delete assignments")

    assignment.delete()

    return HttpResponseRedirect(f"/class/{classid}/assignments")


@transaction.atomic
@login_required
@require_POST
def deleteclass(request: HttpRequest, classid: int) -> HttpResponseRedirect:
    class_auth = auth_class(request, classid)
    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to delete class")

    class_auth.connectedclass.deleted = True
    class_auth.connectedclass.save()
    deleteclass_background(class_auth.connectedclass.id)

    return HttpResponseRedirect("/")


@transaction.atomic
@login_required
@require_POST
def breed_herd(
    request: HttpRequest, classid: int, herdid: int
) -> HttpResponseRedirect:
    form = forms.BreedHerd(request.POST)
    class_auth = auth_class(request, classid, "class_herd")
    herd_auth = auth_herd(class_auth, herdid, "connectedclass")

    if type(class_auth) is not ClassAuth.Student:
        raise Http404("Must be student to breed herd")

    if type(herd_auth) is not HerdAuth.EnrollmentHerd:
        raise Http404("Must be enrollment herd to breed")

    if form.is_valid(class_auth):
        breeding_results = form.save(herd_auth)

        if breeding_results.age_deaths == 1:
            messages.info(request, "There was one death due to old age.")
        else:
            messages.info(
                request,
                f"There were {breeding_results.age_deaths} deaths due to old age.",
            )

        if breeding_results.recessive_deaths == 1:
            messages.info(
                request,
                "There was one death due to undesirable genetic recessives.",
            )
        else:
            messages.info(
                request,
                f"There were {breeding_results.recessive_deaths} deaths due to undesirable genetic recessives.",
            )

        return HttpResponseRedirect(f"/class/{classid}/herd/{herdid}")
    else:
        raise Http404(f"Breeding is invalid: {form.errors}")


@transaction.atomic
@login_required
def genomic_test(request: HttpRequest, classid: int) -> HttpResponseRedirect:
    class_auth = auth_class(request, classid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to genomic test")

    class_auth.connectedclass.recalculate_ptas(
        classid, request.user.email, True
    )

    return HttpResponseRedirect(f"/class/{classid}/running-genomic-test")


@transaction.atomic
@login_required
def calculate_ptas(request: HttpRequest, classid: int) -> HttpResponseRedirect:
    class_auth = auth_class(request, classid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to calculate ptas")

    class_auth.connectedclass.recalculate_ptas(
        class_auth.connectedclass.id, request.user.email
    )

    return HttpResponseRedirect(f"/class/{classid}/running-calculate-ptas")


@transaction.atomic
@login_required
@require_POST
def submit_animal(
    request: HttpRequest, classid: int, herdid: int, animalid: int
) -> HttpResponseRedirect:
    form = forms.SubmitAnimal(request.POST)
    class_auth = auth_class(request, classid)
    herd_auth = auth_herd(class_auth, herdid)
    animal = get_object_or_404(
        models.Animal.objects.defer("pedigree"),
        connectedclass=classid,
        herd=herdid,
        id=animalid,
    )

    if type(class_auth) is not ClassAuth.Student:
        raise Http404("Must be student to submit animal")

    if type(herd_auth) is not HerdAuth.EnrollmentHerd:
        raise Http404("Must be enrollment herd to submit animal")

    if form.is_valid(class_auth):
        form.save(class_auth, animal)
        return HttpResponseRedirect(f"/class/{classid}/herd/{herdid}")
    else:
        raise Http404(f"Animal submission is invalid: {form.errors}")


@login_required
@transaction.atomic
@require_POST
def confirm_enrollment(
    request: HttpRequest, classid: int, requestid: int
) -> JsonResponse:
    class_auth = auth_class(request, classid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to confirm enrollment")

    if class_auth.connectedclass.enrollment_tokens > 0:
        enrollment_request = get_object_or_404(
            models.EnrollmentRequest.objects.select_related(
                "connectedclass", "student"
            ),
            id=requestid,
            connectedclass=class_auth.connectedclass,
        )

        enrollment = models.Enrollment.create_from_enrollment_request(
            enrollment_request
        )

        data = enrollment.json_dict()
    else:
        data = {"out of tokens": True}

    return JsonResponse(data)


@login_required
@transaction.atomic
@require_POST
def remove_enrollment(
    request: HttpRequest, classid: int, enrollmentid: int
) -> JsonResponse:
    class_auth = auth_class(request, classid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to remove enrollment")

    enrollment = get_object_or_404(
        models.Enrollment,
        id=enrollmentid,
        connectedclass=class_auth.connectedclass,
    )
    enrollment.delete()

    return JsonResponse({})


@login_required
@transaction.atomic
@require_POST
def deny_enrollment(
    request: HttpRequest, classid: int, requestid: int
) -> JsonResponse:
    class_auth = auth_class(request, classid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to deny enrollment")

    enrollment_request = get_object_or_404(
        models.EnrollmentRequest,
        id=requestid,
        connectedclass=class_auth.connectedclass,
    )
    enrollment_request.delete()

    return JsonResponse({})


@login_required
def generating_file(request: HttpRequest, classid: int) -> HttpResponse:
    class_auth = auth_class(request, classid)

    return render(
        request,
        "base/generatingfile.html",
        {"class": class_auth.connectedclass},
    )


@login_required
def genomic_test_running(request: HttpRequest, classid: int) -> HttpResponse:
    class_auth = auth_class(request, classid)

    return render(
        request,
        "base/genomic_test_running.html",
        {"class": class_auth.connectedclass},
    )


@login_required
def pta_calculation_running(
    request: HttpRequest, classid: int
) -> HttpResponse:
    class_auth = auth_class(request, classid)

    return render(
        request,
        "base/pta_calculation_running.html",
        {"class": class_auth.connectedclass},
    )


#### FILE VIEWS ####
@login_required
def get_trend_chart(request: HttpRequest, classid: int) -> FileResponse:
    class_auth = auth_class(request, classid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to get trend chart")

    traitset = Traitset(class_auth.connectedclass.traitset)
    headers = (
        ["Time Stamp", "Population Size", "Net Merit $"]
        + [
            filter_text_to_default(f"<{x.uid}>", class_auth.connectedclass)
            for x in traitset.traits
        ]
        + [
            filter_text_to_default(f"ph: <{x.uid}>", class_auth.connectedclass)
            for x in traitset.traits
        ]
    )
    data = []
    for row in class_auth.connectedclass.trend_log:
        data.append(
            [
                row[nms.TIME_STAMP_KEY],
                row[nms.POPULATION_SIZE_KEY],
                row[nms.NETMERIT_KEY],
            ]
            + [row["genotype"][x.uid] for x in traitset.traits]
            + [row["phenotype"][x.uid] for x in traitset.traits]
        )

    return csv.create_csv_response("trendlog.csv", headers, data)


@login_required
@transaction.atomic()
def get_animal_chart(
    request: HttpRequest, classid: int
) -> HttpResponseRedirect:
    class_auth = auth_class(request, classid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to get animal chart")

    csv.create_animal_csv(classid, request.user.id)

    return HttpResponseRedirect(f"/class/{classid}/generating-file")


#### JSON VIEWS ####
@login_required
def get_enrollments(request: HttpRequest, classid: int) -> JsonResponse:
    class_auth = auth_class(request, classid)

    if type(class_auth) not in ClassAuth.TEACHER_ADMIN:
        raise Http404("Must be teacher to get_enrollments")

    json = {
        "enrollments": [
            x.json_dict()
            for x in models.Enrollment.objects.select_related(
                "student"
            ).filter(connectedclass=class_auth.connectedclass)
        ],
        "enrollment_requests": [
            x.json_dict()
            for x in models.EnrollmentRequest.objects.select_related(
                "student"
            ).filter(connectedclass=class_auth.connectedclass)
        ],
    }

    return JsonResponse(json)


@login_required
def get_herd(request: HttpRequest, classid: int, herdid: int) -> JsonResponse:
    class_auth = auth_class(request, classid, "class_herd")
    herd_auth = auth_herd(class_auth, herdid)
    json = herd_auth.herd.json_dict()

    return JsonResponse(json)


@login_required
def get_assignments(
    request: HttpRequest, classid: int, herdid: int
) -> JsonResponse:
    class_auth = auth_class(request, classid, "class_herd")
    herd_auth = auth_herd(class_auth, herdid)

    if type(herd_auth) is not HerdAuth.EnrollmentHerd:
        raise Http404("Invalid herd to collect assignments for.")

    assignments = class_auth.enrollment.get_open_assignments_json_dict()
    return JsonResponse(assignments)


@login_required
def get_breeding_validation(
    request: HttpRequest, classid: int, herdid: int
) -> JsonResponse:
    form = forms.BreedHerd(request.GET)
    class_auth = auth_class(request, classid)
    herd_auth = auth_herd(class_auth, herdid)

    if type(class_auth) is not ClassAuth.Student:
        raise Http404("Must be student to breed herd")

    if type(herd_auth) is not HerdAuth.EnrollmentHerd:
        raise Http404("Must be enrollment herd to breed")

    if form.is_valid(class_auth):
        return JsonResponse({"status": "pass"})
    else:
        return JsonResponse({"status": "fail"})


@login_required
def get_pedigree(
    request: HttpRequest, classid: int, herdid: int, animalid: int
) -> JsonResponse:
    class_auth = auth_class(request, classid, "class_herd")
    herd_auth = auth_herd(class_auth, herdid)
    animal = get_object_or_404(
        models.Animal.objects, id=animalid, herd=herd_auth.herd
    )

    return JsonResponse(animal.pedigree)
