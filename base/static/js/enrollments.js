async function getEnrollments(classid) {
    return $.ajax({
        dataType: "json",
        url: `/class/${classid}/get-enrollments`,
    }).fail((err) => {
        sendMessage("Error: Could not load enrollments.", null, true);
        console.log(err);
    });
}

function getCSRFData() {
    return {
        csrfmiddlewaretoken: $("*[name=csrfmiddlewaretoken]").val()
    };
}

function confirmEnrollment(classId, enrollmentRequestId) {
    $.ajax({
        dataType: "json",
        url: `/class/${classId}/enrollments/requests/${enrollmentRequestId}/confirm`,
        data: getCSRFData(),
        method: "POST",
        success: (data) => {
            if (data["out of tokens"]) {
                sendMessage("Out of enrollment tokens. Cannot confirm enrollment until more are issued.", null, true);
            } else {
                $("#id_enrollment_tokens").val($("#id_enrollment_tokens").val() - 1);
                $(`#enrollment-request-${enrollmentRequestId}`).replaceWith(
                    buildEnrollmentCard(
                        data["student"]["name"],
                        data["student"]["email"],
                        data["id"],
                        data["connectedclass"],
                        data["herd"]
                    )
                );

                $("#tokens").val($("#tokens").val() - 1)

                filterAll();
            }
        }
    }).fail((err) => {
        sendMessage("Error: Could not confirm enrollment.", null, true);
        console.log(err);
    });
}

function denyEnrollment(classId, enrollmentRequestId) {
    if (!confirm("Are you sure you want to deny this enrollment?")) return;

    $.ajax({
        dataType: "json",
        data: getCSRFData(),
        url: `/class/${classId}/enrollments/requests/${enrollmentRequestId}/deny`,
        method: "POST",
        success: () => { $(`#enrollment-request-${enrollmentRequestId}`).remove(); }
    }).fail((err) => {
        sendMessage("Error: Could not deny enrollment.", null, true);
        console.log(err);
    });
}

function removeEnrollment(classId, enrollmentId) {
    if (!confirm("Are you sure you want to remove this enrollment?")) return;

    $.ajax({
        dataType: "json",
        data: getCSRFData(),
        url: `/class/${classId}/enrollments/${enrollmentId}/remove`,
        method: "POST",
        success: () => { $(`#enrollment-${enrollmentId}`).remove(); }
    }).fail((err) => {
        sendMessage("Error: Could not remove enrollment.", null, true);
        console.log(err);
    });
}

function buildEnrollmentCard(studentName, studentEmail, enrollmentId, classId, herdId) {
    let div = $("<div></div>", {
        id: `enrollment-${enrollmentId}`,
        class: ["grid-auto-row", "gap"].join(" ")
    });
    let p = $("<p></p>");
    p.text(`${studentName} (${studentEmail})`);

    let button = $("<button></button>", { class: ["as-btn", "pad", "background-red", "border-radius"].join(" ") });
    button.click(() => { removeEnrollment(classId, enrollmentId); });
    button.text("Remove");

    let a = $("<a></a>", {
        class: ["as-btn", "pad", "background-green", "border-radius"].join(" "),
        href: `/class/${classId}/herd/${herdId}`,
        autofilter: true,
    });
    a.text("View <herd>");

    div.append(p);
    div.append(a);
    div.append(button);
    return div;
}

function buildEnrollmentRequestCard(studentName, studentEmail, enrollmentRequestId, classId) {
    let div = $("<div></div>", {
        id: `enrollment-request-${enrollmentRequestId}`,
        class: ["grid-auto-row", "gap"].join(" ")
    });
    let p = $("<p></p>");
    p.text(`${studentName} (${studentEmail})`);

    let button1 = $("<button></button>", { class: ["as-btn", "pad", "background-green", "border-radius"].join(" ") });
    button1.click(() => { confirmEnrollment(classId, enrollmentRequestId); });
    button1.text("Accept");

    let button2 = $("<button></button>", { class: ["as-btn", "pad", "background-red", "border-radius"].join(" ") });
    button2.click(() => { denyEnrollment(classId, enrollmentRequestId); });
    button2.text("Deny");


    div.append(p);
    div.append(button1);
    div.append(button2);
    return div;
}

async function loadEnrollmentList(enrollments) {
    enrollments = await enrollments;
    enrollments["enrollments"].forEach((e) => {
        $("#enrollments").append(
            buildEnrollmentCard(
                e["student"]["name"],
                e["student"]["email"],
                e["id"],
                e["connectedclass"],
                e["herd"]
            )
        );
    });
    enrollments["enrollment_requests"].forEach((e) => {
        $("#enrollments").append(
            buildEnrollmentRequestCard(
                e["student"]["name"],
                e["student"]["email"],
                e["id"],
                e["connectedclass"]
            )
        );
    });

    clearLoadingSymbol("enrollments");
    filterAll();
}
