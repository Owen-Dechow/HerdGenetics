function addOutOfEnrollmentTokensWarning() {
    let enrollmentTokens = $("#id_enrollment_tokens");
    const nRemaining = enrollmentTokens.val();
    
    if (nRemaining <= 5) {
        sendMessage(
            "You are running low on enrollment tokens "
            + `(${nRemaining} remaining). You are not `
            + "able to accept any enrollments once you "
            + "have run out. "
            + 'Please use the "Request More Enrollment Tokens" page under "Management" to '
            + "get more enrollment tokens",
        );
    }

    if (nRemaining == 0) {
        sendMessage(
            "You can not accept any enrollment request "
            + "until you have more enrollment tokens. "
            + 'Please use the "Request More Enrollment Tokens" page under "Management" to '
            + "get more enrollment tokens",
            null,
            true,
        );
    }
}
