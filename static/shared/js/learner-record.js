/* One learner: a recommendation for academic enhancement in every subject, and the record for the school year.
 *
 * ArmsLearner.open(url, tab) fetches the learner from `url` (teachers:learner_record or heads-learner-record), shows the
 * computed recommendations at once, then polls Poro AI's wording (the response's `poll` URL) and swaps each one in when
 * it has passed its checks. tab: 'advice' (the Action button) or 'record' (a click on the row).
 * The dialog's markup is templates/shared/_learner_modal.html; its look is static/shared/css/learner-record.css.
 */
(function () {
    'use strict';
    var modal = document.getElementById('lrModal');
    if (!modal) return;
    var poroImg = modal.dataset.poroImg || '';
    var title = document.getElementById('lrTitle'), sub = document.getElementById('lrSub');
    var panes = {}, tabs = {};
    modal.querySelectorAll('[data-lr-pane]').forEach(function (p) { panes[p.dataset.lrPane] = p; });
    modal.querySelectorAll('[data-lr-tab]').forEach(function (b) { tabs[b.dataset.lrTab] = b; });
    var esc = function (v) { return String(v == null ? '' : v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'); };
    var fmt = function (n) { return n == null ? '&mdash;' : (Number.isInteger(n) ? String(n) : Number(n).toFixed(1)); };
    var grade = function (n) { return n == null ? '<span class="grade none">&mdash;</span>' : '<span class="grade ' + (n >= 75 ? 'pass' : 'fail') + '">' + fmt(n) + '</span>'; };
    var opener = null, current = 0, cache = {};
    var FIRST = 3;                                              // recommendations shown before "Show the other N"

    function show(tab) {
        Object.keys(panes).forEach(function (k) { panes[k].hidden = k !== tab; tabs[k].setAttribute('aria-selected', k === tab ? 'true' : 'false'); });
    }
    Object.keys(tabs).forEach(function (k) { tabs[k].addEventListener('click', function () { show(k); }); });

    function setText(el, text) {
        if (!el || el.textContent === text) return;
        el.classList.add('fading');
        setTimeout(function () { el.textContent = text; el.classList.remove('fading'); }, 250);
    }
    function source(text, ai) {
        var s = panes.advice.querySelector('.lr-src');
        if (s) { s.textContent = text; s.className = 'lr-src' + (ai ? ' ai' : ''); }
    }

    function drawAdvice(d) {
        if (!d.recs.length) {
            panes.advice.innerHTML = '<p class="lr-none">No subject has a ' + esc(d.period_name) + ' grade yet, so there is nothing to recommend.</p>';
            return;
        }
        // the most urgent first (remediation, then keeping a pass, moving up, enrichment); FIRST of them show, the rest on demand
        var card = function (r, i) {
            return '<section class="lr-rec level-' + esc(r.level.key) + '"' + (i >= FIRST && !d.all ? ' hidden' : '') + '>' +
                '<div class="lr-rec-head"><b>' + esc(r.code) + '<small>' + esc(r.name) + '</small></b>' + grade(r.grade) +
                '<span class="lr-lvl">' + esc(r.level.name) + '</span></div>' +
                '<div class="lr-parts">' + r.parts.map(function (c) {
                    return '<span' + (c.weak ? ' class="weak" title="Weakest part"' : '') + '>' + esc(c.abbr) + ' ' + (c.value == null ? '&mdash;' : esc(c.value) + '%') + '</span>';
                }).join('') + '</div>' +
                '<div class="lr-advice">' + (poroImg ? '<img src="' + esc(poroImg) + '" alt="">' : '') +
                '<p data-field="' + esc(r.key) + '">' + esc((d.written || {})[r.key] || r.text) + '</p></div></section>';
        };
        var rest = d.recs.length - FIRST;
        panes.advice.innerHTML = '<p class="hint lr-lead">' + (d.recs.length > FIRST ? 'The ' + FIRST + ' subjects that need the most attention first.' :
                'Every subject, the one that needs the most attention first.') + '</p>' +
            '<div class="lr-recs">' + d.recs.map(card).join('') + '</div>' +
            (rest > 0 ? '<button type="button" class="btn btn-line btn-sm lr-more" aria-expanded="' + (d.all ? 'true' : 'false') + '">' +
                (d.all ? 'Show fewer' : 'Show the other ' + rest + ' subject' + (rest === 1 ? '' : 's')) + '</button>' : '') +
            '<p class="lr-src">' + esc(d.done || 'Decided from the grades by rules a person can check') + '</p>';
        var more = panes.advice.querySelector('.lr-more');
        if (more) more.addEventListener('click', function () { d.all = !d.all; drawAdvice(d); });
        if (d.done) source(d.done, d.ai);
    }

    function drawRecord(d) {
        var at = d.periods.map(function (p) { return p.label; }).indexOf(d.period);
        var head = '<tr><th>Subject</th>' + d.periods.map(function (p, j) { return '<th class="center' + (j === at ? ' cur' : '') + '">' + esc(p.label) + '</th>'; }).join('') +
            '<th class="center">Final</th>' + d.components.map(function (c) { return '<th class="center" title="' + esc(c.name) + ', ' + esc(d.period_name) + '">' + esc(c.abbr) + '</th>'; }).join('') + '</tr>';
        var rows = d.rows.map(function (r) {
            return '<tr><td><b>' + esc(r.code) + '</b> <span class="muted">' + esc(r.name) + '</span></td>' +
                r.grades.map(function (g, j) { return '<td class="center' + (j === at ? ' cur' : '') + '">' + grade(g) + '</td>'; }).join('') +
                '<td class="center">' + grade(r.final) + '</td>' +
                [0, 1, 2].map(function (k) { return '<td class="center muted">' + (r.parts ? fmt(r.parts[k]) : '&mdash;') + '</td>'; }).join('') + '</tr>';
        }).join('');
        var foot = '<tr><td>General average</td>' + d.averages.map(function (a, j) { return '<td class="center' + (j === at ? ' cur' : '') + '">' + fmt(a) + '</td>'; }).join('') +
            '<td></td>' + d.components.map(function () { return '<td></td>'; }).join('') + '</tr>';
        panes.record.innerHTML = '<p class="hint" style="margin:0 0 0.6rem">' + d.components.map(function (c) { return esc(c.abbr); }).join(', ') +
            ' are the ' + esc(d.period_name) + ' component scores in percent.</p><div style="overflow-x:auto"><table class="table lr-table"><thead>' +
            head + '</thead><tbody>' + rows + '</tbody><tfoot>' + foot + '</tfoot></table></div>';
    }

    function poll(d, ticket) {
        if (!d.poll || d.finished) return;
        var tries = 0;
        var step = function () {
            fetch(d.poll, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }).then(function (r) { return r.ok ? r.json() : null; }).then(function (s) {
                if (!s || ticket !== current) return;
                d.written = Object.assign(d.written || {}, s.fields || {});
                Object.keys(s.fields || {}).forEach(function (k) { setText(panes.advice.querySelector('[data-field="' + k + '"]'), s.fields[k]); });
                if (s.state === 'writing') {
                    source('Poro AI is writing the advice' + (s.total ? ' (' + (s.done || 0) + ' of ' + s.total + ')' : '') + '…');
                    if (++tries <= 150) setTimeout(step, 4000);
                    return;
                }
                d.finished = true;
                d.ai = s.source === 'ai';
                d.done = d.ai ? 'Written by Poro AI for ' + s.written + ' of ' + s.total + ' subject' + (s.total === 1 ? '' : 's') + '; checked before it is shown'
                              : 'Decided from the grades by rules a person can check';
                source(d.done, d.ai);
            });
        };
        step();
    }

    function open(url, tab, from) {
        opener = from || document.activeElement;
        var ticket = ++current;
        title.textContent = 'Learner';
        sub.textContent = '';
        panes.advice.innerHTML = panes.record.innerHTML = '<p class="lr-loading"><i class="fi fi-rr-spinner"></i>Loading the learner…</p>';
        show(tab === 'record' ? 'record' : 'advice');
        modal.hidden = false;
        document.body.style.overflow = 'hidden';
        modal.querySelector('.tp-modal-card').focus();
        var got = cache[url] ? Promise.resolve(cache[url]) : fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
        got.then(function (d) {
            if (ticket !== current) return;
            cache[url] = d;
            title.textContent = d.name;
            sub.textContent = [d.lrn ? 'LRN ' + d.lrn : '', d.section, d.period_name].filter(Boolean).join(' · ');
            drawAdvice(d);
            drawRecord(d);
            poll(d, ticket);
        }).catch(function () {
            if (ticket !== current) return;
            panes.advice.innerHTML = panes.record.innerHTML = '<p class="lr-none">This learner could not be loaded. Close this and try again.</p>';
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
