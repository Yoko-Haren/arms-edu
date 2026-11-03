/* The principal and registrar forms of the admin portal fill themselves in.
   The school picks the email domain; the name gives firstname.lastname@domain (the registrar's default is
   shortname.registrar@domain); the server numbers an address that is taken (admin_panel.views.suggest_account).
   The temporary password is generated, and can be made again or copied before the account exists. */
(function () {
    var form = document.querySelector('form[data-account-form]');
    if (!form) return;
    var role = form.dataset.accountForm, suggestUrl = form.dataset.suggestUrl;
    var schools = JSON.parse(document.getElementById('schoolData').textContent);
    var school = form.querySelector('[name="school"]'), first = form.querySelector('[name="first_name"]'), last = form.querySelector('[name="last_name"]');
    var email = form.querySelector('[name="email"]'), password = form.querySelector('[name="password"]');
    var emailHint = document.getElementById('emailHint'), schoolNote = document.getElementById('schoolNote');
    var suggested = null, timer = null, asked = 0;

    var UPPER = 'ABCDEFGHJKMNPQRSTUVWXYZ', LOWER = 'abcdefghjkmnpqrstuvwxyz', DIGITS = '23456789';
    function pick(set) { var a = new Uint32Array(1); crypto.getRandomValues(a); return set[a[0] % set.length]; }
    function word() { return pick(UPPER) + pick(LOWER) + pick(LOWER) + pick(LOWER); }
    function newPassword() { return word() + '-' + pick(DIGITS) + pick(DIGITS) + pick(DIGITS) + pick(DIGITS) + '-' + word(); }

    function info() { return schools[school.value] || null; }
    function set(key, value) { document.querySelectorAll('[data-preview="' + key + '"]').forEach(function (el) { el.textContent = value; }); }
    function fullName() {
        var f = first.value.trim(), l = last.value.trim(), s = info();
        if (f && l) return f + ' ' + l;
        if (role === 'registrar' && s) return (s.short || 'School') + ' Registrar';
        return 'Type the name';
    }

    function render() {
        var s = info(), typed = email.value.trim().toLowerCase(), problem = '';
        if (!s) {
            emailHint.textContent = 'Choose the school first.';
        } else if (typed) {
            emailHint.textContent = 'This email is also the username.';
        } else if (suggested) {
            emailHint.textContent = 'Leave it blank to use ' + suggested + '.';
        } else if (s.domain) {
            emailHint.textContent = role === 'registrar' ? 'Leave it blank to use the registrar address of ' + s.domain + '.' : 'Type the name and ARMS makes it at ' + s.domain + '.';
        } else {
            problem = s.name + ' has no email domain in ARMS, so type the ' + (role === 'registrar' ? 'registrar' : 'principal') + '’s email. It is also their username.';
            emailHint.textContent = problem;
        }
        email.placeholder = suggested || (s && s.domain ? 'name@' + s.domain : 'name@example.com');
        email.required = !!s && !s.domain;
        emailHint.classList.toggle('warn-text', !!problem);
        if (schoolNote) {
            var staff = s ? s.staff : [];
            schoolNote.hidden = !staff.length;
            schoolNote.querySelector('span').textContent = staff.length ? s.name + ' already has ' + (role === 'registrar' ? 'a registrar' : 'a School Head') + ': ' + staff.join(', ') + '. You can still add another.' : '';
        }
        set('name', fullName());
        set('school', s ? s.name : 'Choose a school');
        set('email', typed || suggested || (s && !s.domain ? 'Type an email' : '—'));
        set('password', password.value || 'Made when you create it');
    }

    // ask the server for the address it would make, after typing pauses
    function ask() {
        clearTimeout(timer);
        timer = setTimeout(function () {
            if (!school.value) { suggested = null; render(); return; }
            var n = ++asked, q = new URLSearchParams({ school: school.value, role: role, first: first.value, last: last.value, email: email.value.trim() });
            fetch(suggestUrl + '?' + q.toString(), { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (n !== asked) return;
                    suggested = d.email;
                    render();
                    var bad = !!d.taken;
                    email.closest('.field').classList.toggle('bad', bad);
                    if (bad) emailHint.textContent = 'That email already has an ARMS account.';
                    emailHint.classList.toggle('warn-text', bad || emailHint.classList.contains('warn-text'));
                }).catch(function () {});
        }, 250);
    }
    [school, first, last, email].forEach(function (el) { el.addEventListener(el.tagName === 'SELECT' ? 'change' : 'input', ask); });
    password.addEventListener('input', render);

    var again = document.getElementById('pwAgain'), copy = document.getElementById('pwCopy');
    if (!password.value) password.value = newPassword();
    if (again) again.addEventListener('click', function () { password.value = newPassword(); render(); });
    if (copy) copy.addEventListener('click', function () {
        navigator.clipboard.writeText(password.value).then(function () {
            var label = copy.querySelector('span'), was = label.textContent;
            label.textContent = 'Copied';
            setTimeout(function () { label.textContent = was; }, 1500);
        });
    });
    form.addEventListener('submit', function () {
        var button = form.querySelector('button[type="submit"]');
        if (button) { button.disabled = true; button.innerHTML = '<i class="fi fi-rr-refresh"></i> Creating…'; }
    });
    render();
    ask();
})();
