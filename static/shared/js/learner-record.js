/* One learner, as View opens them: the average and performance level, the record, and a recommendation for academic
 * enhancement in every subject, one row per subject; Print opens the same as a page to print or save as PDF.
 *
 * ArmsLearner.open(url, from) fetches the learner from `url` (teachers:learner_record or heads-learner-record, with the
 * page's Period choice), shows the computed recommendations at once, then polls Poro AI's wording (the response's `poll`
 * URL) and swaps each one in when it has passed its checks. The record follows the choice: one period shows that period
 * and its components; All shows every period and the final grade; Final shows the final grades.
 * The dialog's markup is templates/shared/_learner_modal.html; its look is static/shared/css/learner-record.css.
 */
(function () {
    'use strict';
    var modal = document.getElementById('lrModal');
    if (!modal) return;
    var poroImg = modal.dataset.poroImg || '';
    var title = document.getElementById('lrTitle'), sub = document.getElementById('lrSub');
    var body = document.getElementById('lrBody'), printLink = document.getElementById('lrPrint');
    var esc = function (v) { return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); };
    var fmt = function (n) { return n == null ? '&mdash;' : (Number.isInteger(n) ? String(n) : Number(n).toFixed(1)); };
    var grade = function (n) { return n == null ? '<span class="grade none">&mdash;</span>' : '<span class="grade ' + (n >= 75 ? 'pass' : 'fail') + '">' + fmt(n) + '</span>'; };
    var opener = null, current = 0, cache = {};
    var FIRST = 3;                                              // recommendations shown before "Show the other N"
    var RULES = 'Decided from the grades by rules a person can check';

    function setText(el, text) {
        if (!el || el.textContent === text) return;
        el.classList.add('fading');
        setTimeout(function () { el.textContent = text; el.classList.remove('fading'); }, 250);
    }
    function source(text, ai) {
        var s = body.querySelector('.lr-src');
        if (s) { s.textContent = text; s.className = 'lr-src' + (ai ? ' ai' : ''); }
    }

    // the learner's average and performance level for the period chosen
    function summary(d) {
        if (d.average == null) return '';
        return '<div class="lr-summary level-' + esc(d.level.key) + '">' +
            '<span class="lr-badge" aria-hidden="true">' + esc(d.level.letter) + '</span>' +
            '<div class="lr-sum-level"><b>' + esc(d.level.name) + '</b><span>Performance level &middot; ' + esc(d.view_label) + '</span></div>' +
            '<div class="lr-stat"><b>' + fmt(d.average) + '</b><span>Average' + (d.view === 'final' ? ' of the final grades' : '') + '</span></div></div>';
    }

    // the record: the grade columns of the choice (a period; every period and Final; Final), then the components
    function record(d) {
        var head = '<tr><th scope="col">Subject</th>' + d.heads.map(function (h) {
            return '<th scope="col" class="center" title="' + esc(h.name) + '">' + esc(h.label) + '</th>';
        }).join('') + d.components.map(function (c) { return '<th scope="col" class="center comp" title="' + esc(c.name) + '">' + esc(c.abbr) + '</th>'; }).join('') + '</tr>';
        var rows = d.rows.map(function (r) {
            return '<tr><td><b>' + esc(r.code) + '</b> <span class="muted">' + esc(r.name) + '</span></td>' +
                r.grades.map(function (g) { return '<td class="center">' + grade(g) + '</td>'; }).join('') +
                [0, 1, 2].map(function (k) { return '<td class="center comp">' + (r.parts ? fmt(r.parts[k]) : '&mdash;') + '</td>'; }).join('') + '</tr>';
        }).join('');
        var foot = '<tr><td>General average</td>' + d.averages.map(function (a) { return '<td class="center">' + fmt(a) + '</td>'; }).join('') +
            d.components.map(function () { return '<td></td>'; }).join('') + '</tr>';
        return '<section class="lr-sec" aria-labelledby="lrRecTitle"><h4 id="lrRecTitle"><i class="fi fi-rr-table-list"></i> Record &middot; ' + esc(d.view_label) + '</h4>' +
            '<p class="hint">' + d.components.map(function (c) { return esc(c.abbr); }).join(', ') + ' are ' + esc(d.parts_note) + ', in percent.</p>' +
            '<div class="lr-scroll"><table class="table lr-table"><thead>' + head + '</thead><tbody>' + rows + '</tbody><tfoot>' + foot + '</tfoot></table></div></section>';
    }

    // one row per subject: the grade, the level and what to do; the most urgent first, FIRST of them until asked for the rest
    function advice(d) {
        var head = '<h4 id="lrAdvTitle">' + (poroImg ? '<img src="' + esc(poroImg) + '" alt="">' : '') + 'Poro AI&rsquo;s recommendations</h4>';
        if (!d.recs.length) {
            return '<section class="lr-sec" aria-labelledby="lrAdvTitle">' + head +
                '<p class="lr-none">No subject has ' + (d.view === 'final' ? 'a final grade' : d.view === 'all' ? 'a grade' : 'a ' + esc(d.view_label) + ' grade') +
                ' yet, so there is nothing to recommend.</p></section>';
        }
        var rest = d.recs.length - FIRST;
        var rows = d.recs.map(function (r, i) {
            return '<tr class="level-' + esc(r.level.key) + '"' + (i >= FIRST && !d.all ? ' hidden' : '') + '>' +
                '<td><b>' + esc(r.code) + '</b><small>' + esc(r.name) + '</small></td>' +
                '<td class="center">' + grade(r.grade) + '</td>' +
                '<td><span class="lr-lvl">' + esc(r.level.name) + '</span>' + (r.weak ? '<small>Weakest: ' + esc(r.weak) + '</small>' : '') + '</td>' +
                '<td><p data-field="' + esc(r.key) + '">' + esc((d.written || {})[r.key] || r.text) + '</p></td></tr>';
        }).join('');
        return '<section class="lr-sec" aria-labelledby="lrAdvTitle">' + head +
            '<p class="lr-by"><i class="fi fi-rr-brain" aria-hidden="true"></i>Recommended by ' + esc(d.model || 'Poro AI') + '.</p>' +
            '<p class="hint">' + (d.recs.length > FIRST ? 'The ' + FIRST + ' subjects that need the most attention first.' :
                'Every subject, the one that needs the most attention first.') + '</p>' +
            '<div class="lr-scroll"><table class="table lr-rtable"><thead><tr><th scope="col">Subject</th><th scope="col" class="center">Grade</th>' +
            '<th scope="col">Level</th><th scope="col">Recommendation</th></tr></thead><tbody>' + rows + '</tbody></table></div>' +
            (rest > 0 ? '<button type="button" class="btn btn-line btn-sm lr-more" aria-expanded="' + (d.all ? 'true' : 'false') + '">' +
                (d.all ? 'Show fewer' : 'Show the other ' + rest + ' subject' + (rest === 1 ? '' : 's')) + '</button>' : '') +
            '<p class="lr-src' + (d.ai ? ' ai' : '') + '">' + esc(d.done || RULES) + '</p></section>';
    }

    function draw(d) {
        body.innerHTML = summary(d) + record(d) + advice(d);
        var more = body.querySelector('.lr-more');
        if (more) more.addEventListener('click', function () {
            d.all = !d.all;
            body.querySelectorAll('.lr-rtable tbody tr').forEach(function (tr, i) { tr.hidden = i >= FIRST && !d.all; });
            var rest = d.recs.length - FIRST;
            more.textContent = d.all ? 'Show fewer' : 'Show the other ' + rest + ' subject' + (rest === 1 ? '' : 's');
            more.setAttribute('aria-expanded', d.all ? 'true' : 'false');
        });
    }

    function poll(d, ticket) {
        if (!d.poll || d.finished) return;
        var tries = 0;
        var step = function () {
            fetch(d.poll, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }).then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
                if (!s || ticket !== current) return;
                d.written = Object.assign(d.written || {}, s.fields || {});
                Object.keys(s.fields || {}).forEach(function (k) { setText(body.querySelector('[data-field="' + k + '"]'), s.fields[k]); });
                if (s.state === 'writing') {
                    source('Poro AI is writing the advice' + (s.total ? ' (' + (s.done || 0) + ' of ' + s.total + ')' : '') + '…');
                    if (++tries <= 150) setTimeout(step, 4000);
                    return;
                }
                d.finished = true;
                d.ai = s.source === 'ai';
                d.done = d.ai ? 'Worded by Poro AI' + (s.model ? ' (' + s.model + ')' : '') + ' for ' + s.written + ' of ' + s.total + ' subject' + (s.total === 1 ? '' : 's') + '; checked before it is shown'
                              : RULES;
                source(d.done, d.ai);
            });
        };
        step();
    }

    function open(url, from) {
        if (typeof from === 'string') from = arguments[2];          // the older open(url, tab, from)
        opener = from || document.activeElement;
        var ticket = ++current;
        title.textContent = 'Learner';
        sub.textContent = '';
        printLink.hidden = true;
        body.innerHTML = '<p class="lr-loading"><i class="fi fi-rr-spinner"></i>Loading the learner…</p>';
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        modal.querySelector('.tp-modal-card').focus();
        var got = cache[url] ? Promise.resolve(cache[url]) : fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
        got.then(function (d) {
            if (ticket !== current) return;
            cache[url] = d;
            title.textContent = d.name;
            sub.textContent = [d.lrn ? 'LRN ' + d.lrn : '', d.section, d.view_label].filter(Boolean).join(' · ');
            if (d.print) { printLink.href = d.print; printLink.hidden = false; }
            draw(d);
            poll(d, ticket);
        }).catch(function () {
            if (ticket !== current) return;
            body.innerHTML = '<p class="lr-none">This learner could not be loaded. Close this and try again.</p>';
        });
    }
    function close() {
        modal.hidden = true;
        document.body.style.overflow = '';
        current++;
        if (opener && opener.focus) opener.focus();
    }
    modal.addEventListener('click', function (e) { if (e.target === modal || e.target.closest('[data-close]')) close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !modal.hidden) close(); });
    window.ArmsLearner = { open: open, close: close };
})();
