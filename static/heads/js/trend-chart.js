/* School Status: the school year's trends in one card (heads/views_performance._trends), one view at a time.
 *
 *   Learner performance  how many learners' averages fall in each level, period by period (100% stacked columns)
 *   Grades               the share of grades at 75 and above, period by period (a line; the average and the grades
 *                        below 75 are in its tooltip and table)
 *   Attendance           days present over school days, month by month (a line; a month still going is marked)
 *
 * Levels are coloured by what they mean: the failing levels in red, darkest for the lowest, the passing levels on a
 * blue ramp, darkest for the highest (both ramps checked with the dataviz validator: monotone lightness, one hue per arm,
 * deutan dE 15 where the two arms meet). Their contrast with the card is low, so each segment carries its share as a
 * label and every view has a Table. Usage: <div data-trends="jsonScriptId"></div>.
 */
(function () {
    'use strict';
    var NS = 'http://www.w3.org/2000/svg';
    var FAIL = { 1: ['#b42318'], 2: ['#b42318', '#ec8a89'] };                  // lowest level first
    var PASS = { 3: ['#86b6ef', '#3987e5', '#104281'], 4: ['#86b6ef', '#5598e7', '#256abf', '#104281'] };
    var LINE = '#2a78d6', INK = '#1a1a2e', MUTED = '#5a6478', GRID = '#e8ecf1';

    function el(tag, attrs, parent) {
        var n = document.createElementNS(NS, tag);
        Object.keys(attrs || {}).forEach(function (k) { n.setAttribute(k, attrs[k]); });
        if (parent) parent.appendChild(n);
        return n;
    }
    function tag(name, cls, parent, text) {
        var n = document.createElement(name);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        if (parent) parent.appendChild(n);
        return n;
    }
    function pct(v) { return v == null ? '—' : (Math.round(v * 10) / 10).toFixed(1) + '%'; }
    function light(hex) {                                       // dark ink on light fills, white on dark ones
        var n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
        return (0.299 * r + 0.587 * g + 0.114 * b) > 150;
    }
    function colours(levels) {
        var failing = levels.filter(function (l) { return !l.passing; }).length, passing = levels.length - failing;
        var f = FAIL[failing] || FAIL[2], p = PASS[passing] || PASS[3];
        return levels.map(function (l, i) { return l.passing ? p[i - failing] : f[i]; });
    }
    function domain(values) {                                   // zoomed to the values, as the forecast chart is
        var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
        lo = Math.max(0, Math.floor(lo - 2)); hi = Math.min(100, Math.ceil(hi + 2));
        while (hi - lo < 8) { if (lo > 0) lo--; if (hi - lo < 8 && hi < 100) hi++; if (lo === 0 && hi === 100) break; }
        var step = hi - lo > 16 ? 5 : 2;
        lo = Math.floor(lo / step) * step; hi = Math.ceil(hi / step) * step;
        return { lo: lo, hi: hi, step: step };
    }

    function render(host, spec) {
        var views = [
            { key: 'performance', name: 'Learner performance', has: spec.performance.length > 0 },
            { key: 'grades', name: 'Grades', has: spec.grades.length > 0 },
            { key: 'attendance', name: 'Attendance', has: spec.attendance.length > 0 }
        ];
        var state = { view: 'performance', table: false };
        try { var saved = localStorage.getItem('arms:trends:view'); if (saved && views.some(function (v) { return v.key === saved; })) state.view = saved; } catch (e) {}
        var bar = tag('div', 'tr-bar', host);
        var tabs = tag('div', 'seg tr-tabs', bar);
        tabs.setAttribute('role', 'radiogroup');
        tabs.setAttribute('aria-label', 'Trend');
        views.forEach(function (v) {
            var b = tag('button', '', tabs, v.name);
            b.type = 'button'; b.setAttribute('role', 'radio'); b.dataset.view = v.key;
            b.addEventListener('click', function () { state.view = v.key; try { localStorage.setItem('arms:trends:view', v.key); } catch (e) {} draw(); });
        });
        var mode = tag('div', 'seg small tr-mode', bar);
        mode.setAttribute('role', 'radiogroup');
        mode.setAttribute('aria-label', 'Show as');
        [['Chart', false], ['Table', true]].forEach(function (m) {
            var b = tag('button', '', mode, m[0]);
            b.type = 'button'; b.setAttribute('role', 'radio'); b.dataset.table = m[1] ? '1' : '';
            b.addEventListener('click', function () { state.table = m[1]; draw(); });
        });
        var note = tag('p', 'hint tr-note', host);
        var legend = tag('div', 'tr-legend', host);
        var stage = tag('div', 'tr-stage', host);
        var tip = tag('div', 'tr-tip', host);
        tip.hidden = true;

        function setTip(html, x, y) {
            tip.innerHTML = html; tip.hidden = false;
            var box = host.getBoundingClientRect(), w = tip.offsetWidth;
            tip.style.left = Math.max(4, Math.min(box.width - w - 4, x - w / 2)) + 'px';
            tip.style.top = Math.max(4, y - tip.offsetHeight - 12) + 'px';
        }
        function hideTip() { tip.hidden = true; }

        function draw() {
            var view = views.filter(function (v) { return v.key === state.view; })[0];
            tabs.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-checked', b.dataset.view === state.view ? 'true' : 'false'); });
            mode.querySelectorAll('button').forEach(function (b) { b.setAttribute('aria-checked', (!!b.dataset.table) === state.table ? 'true' : 'false'); });
            stage.innerHTML = ''; legend.innerHTML = ''; hideTip();
            if (!view.has) { note.textContent = ''; tag('p', 'empty', stage, 'Nothing to show for this school year yet.'); return; }
            ({ performance: performance, grades: grades, attendance: attendance })[state.view]();
        }

        // ---------- learner performance: 100% stacked columns ----------
        function performance() {
            var levels = spec.levels, fills = colours(levels), rows = spec.performance;
            note.textContent = 'How many learners’ averages fall in each level of ' + spec.order + ', period by period.';
            levels.slice().reverse().forEach(function (l, j) {          // the legend reads top to bottom like the stack
                var i = levels.length - 1 - j, k = tag('span', 'tr-key', legend);
                tag('i', '', k).style.background = fills[i];
                tag('span', '', k, l.name + ' (' + l.range + ')');
            });
            if (state.table) {
                var t = tag('table', 'table tr-table', stage), h = tag('tr', '', tag('thead', '', t));
                ['Period'].concat(levels.map(function (l) { return l.name; }), ['Learners']).forEach(function (c, i) { tag('th', i ? 'num' : '', h, c); });
                var b = tag('tbody', '', t);
                rows.forEach(function (r) {
                    var tr = tag('tr', '', b);
                    tag('td', '', tr, r.label);
                    r.counts.forEach(function (n) { tag('td', 'num', tr, n + ' (' + pct(100 * n / (r.total || 1)) + ')'); });
                    tag('td', 'num', tr, String(r.total));
                });
                return;
            }
            var W = Math.max(300, stage.clientWidth || host.clientWidth || 600), H = 300, m = { l: 46, r: 12, t: 12, b: 34 };
            var iw = W - m.l - m.r, ih = H - m.t - m.b, band = iw / rows.length, bw = Math.min(110, band * 0.56);
            var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, width: '100%', height: H, role: 'img',
                                  'aria-label': 'Learners by performance level in each grading period' }, stage);
            [0, 25, 50, 75, 100].forEach(function (v) {
                var y = m.t + ih - ih * v / 100;
                el('line', { x1: m.l, x2: W - m.r, y1: y, y2: y, stroke: GRID, 'stroke-width': 1 }, svg);
                el('text', { x: m.l - 8, y: y + 4, 'text-anchor': 'end', 'font-size': 11, fill: MUTED }, svg).textContent = v + '%';
            });
            rows.forEach(function (r, i) {
                var x = m.l + band * i + (band - bw) / 2, y = m.t + ih, g = el('g', {}, svg);
                r.counts.forEach(function (n, j) {
                    if (!n) return;
                    var hgt = ih * n / (r.total || 1);
                    y -= hgt;
                    el('rect', { x: x, y: y, width: bw, height: Math.max(0, hgt - 2), fill: fills[j] }, g);    // a 2px gap between segments
                    if (hgt >= 18) {
                        el('text', { x: x + bw / 2, y: y + hgt / 2 + 3, 'text-anchor': 'middle', 'font-size': 11, 'font-weight': 600,
                                     fill: light(fills[j]) ? INK : '#fff' }, g).textContent = Math.round(100 * n / r.total) + '%';
                    }
                });
                el('text', { x: x + bw / 2, y: H - 12, 'text-anchor': 'middle', 'font-size': 12, fill: INK }, svg).textContent = r.label;
                var hit = el('rect', { x: m.l + band * i, y: m.t, width: band, height: ih, fill: 'transparent' }, svg);
                hit.addEventListener('mousemove', function (e) {
                    var box = host.getBoundingClientRect();
                    setTip('<b>' + r.label + '</b> &middot; ' + r.total + ' learners' + levels.slice().reverse().map(function (l, j) {
                        var k = levels.length - 1 - j, n = r.counts[k];
                        return '<span class="tr-row"><i style="background:' + fills[k] + '"></i>' + l.name + '<b>' + n + ' (' + pct(100 * n / (r.total || 1)) + ')</b></span>';
                    }).join(''), e.clientX - box.left, e.clientY - box.top);
                });
                hit.addEventListener('mouseleave', hideTip);
            });
        }

        // ---------- one line over periods or months ----------
        function line(points, value, label, tipOf, ariaLabel) {
            var vals = points.map(value);
            var W = Math.max(300, stage.clientWidth || host.clientWidth || 600), H = 280, m = { l: 46, r: 16, t: 16, b: 34 };
            var iw = W - m.l - m.r, ih = H - m.t - m.b, d = domain(vals);
            var pad = 36;                                               // room for the first and last labels
            var x = function (i) { return points.length === 1 ? m.l + iw / 2 : m.l + pad + (iw - 2 * pad) * i / (points.length - 1); };
            var y = function (v) { return m.t + ih - ih * (v - d.lo) / (d.hi - d.lo); };
            var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, width: '100%', height: H, role: 'img', 'aria-label': ariaLabel }, stage);
            for (var v = d.lo; v <= d.hi + 0.001; v += d.step) {
                el('line', { x1: m.l, x2: W - m.r, y1: y(v), y2: y(v), stroke: GRID, 'stroke-width': 1 }, svg);
                el('text', { x: m.l - 8, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: MUTED }, svg).textContent = v + '%';
            }
            var full = points.map(function (p, i) { return [x(i), y(vals[i]), p]; });
            var solid = full.filter(function (c) { return !c[2].partial; });
            if (solid.length > 1) el('path', { d: 'M' + solid.map(function (c) { return c[0] + ',' + c[1]; }).join('L'), fill: 'none', stroke: LINE, 'stroke-width': 2 }, svg);
            var last = solid[solid.length - 1];
            full.forEach(function (c) {
                if (c[2].partial && last) {                             // a month still going: a dashed reach and a hollow point
                    el('path', { d: 'M' + last[0] + ',' + last[1] + 'L' + c[0] + ',' + c[1], fill: 'none', stroke: LINE, 'stroke-width': 2, 'stroke-dasharray': '4 4' }, svg);
                }
                el('circle', { cx: c[0], cy: c[1], r: 4.5, fill: c[2].partial ? '#fff' : LINE, stroke: c[2].partial ? LINE : '#fff', 'stroke-width': 2 }, svg);
                el('text', { x: c[0], y: H - 12, 'text-anchor': 'middle', 'font-size': 12, fill: INK }, svg).textContent = c[2].label + (c[2].partial ? ' (so far)' : '');
            });
            var cross = el('line', { y1: m.t, y2: m.t + ih, stroke: '#9aa5b8', 'stroke-width': 1, 'stroke-dasharray': '3 3', visibility: 'hidden' }, svg);
            var hit = el('rect', { x: m.l - 10, y: m.t, width: iw + 20, height: ih, fill: 'transparent' }, svg);
            hit.addEventListener('mousemove', function (e) {
                var box = svg.getBoundingClientRect(), sx = (e.clientX - box.left) * W / box.width, best = 0;
                full.forEach(function (c, i) { if (Math.abs(c[0] - sx) < Math.abs(full[best][0] - sx)) best = i; });
                var c = full[best], hb = host.getBoundingClientRect();
                cross.setAttribute('x1', c[0]); cross.setAttribute('x2', c[0]); cross.setAttribute('visibility', 'visible');
                setTip(tipOf(c[2]), box.left - hb.left + c[0] * box.width / W, box.top - hb.top + c[1] * box.height / H);
            });
            hit.addEventListener('mouseleave', function () { cross.setAttribute('visibility', 'hidden'); hideTip(); });
            return label;
        }
        function table(head, rows) {
            var t = tag('table', 'table tr-table', stage), h = tag('tr', '', tag('thead', '', t));
            head.forEach(function (c, i) { tag('th', i ? 'num' : '', h, c); });
            var b = tag('tbody', '', t);
            rows.forEach(function (r) { var tr = tag('tr', '', b); r.forEach(function (c, i) { tag('td', i ? 'num' : '', tr, c); }); });
        }

        function grades() {
            note.textContent = 'The share of grades at 75 and above in each grading period. Point at a period for the average grade and the grades below 75.';
            if (state.table) {
                table(['Period', 'Grades at 75 and above', 'Average grade', 'Grades below 75', 'Grades'],
                      spec.grades.map(function (g) { return [g.label, pct(g.passing), g.average.toFixed(1), String(g.below), String(g.grades)]; }));
                return;
            }
            line(spec.grades, function (g) { return g.passing; }, '', function (g) {
                return '<b>' + g.label + '</b><span class="tr-row">At 75 and above<b>' + pct(g.passing) + '</b></span>' +
                    '<span class="tr-row">Average grade<b>' + g.average.toFixed(1) + '</b></span><span class="tr-row">Below 75<b>' + g.below + ' of ' + g.grades + '</b></span>';
            }, 'Share of grades at 75 and above in each grading period');
        }

        function attendance() {
            note.textContent = 'Days present over school days, month by month.' + (spec.attendance.some(function (a) { return a.partial; }) ?
                ' A month marked so far has only a few school days recorded yet.' : '');
            if (state.table) {
                table(['Month', 'Attendance', 'Days present', 'School days'],
                      spec.attendance.map(function (a) { return [a.label + (a.partial ? ' (so far)' : ''), pct(a.rate), a.present.toLocaleString(), a.days.toLocaleString()]; }));
                return;
            }
            line(spec.attendance, function (a) { return a.rate; }, '', function (a) {
                return '<b>' + a.label + (a.partial ? ' (so far)' : '') + '</b><span class="tr-row">Attendance<b>' + pct(a.rate) + '</b></span>' +
                    '<span class="tr-row">Days present<b>' + a.present.toLocaleString() + ' of ' + a.days.toLocaleString() + '</b></span>';
            }, 'Attendance rate in each month of the school year');
        }

        draw();
        var width = host.clientWidth;
        window.addEventListener('resize', function () { if (Math.abs(host.clientWidth - width) > 20) { width = host.clientWidth; draw(); } });
    }

    document.querySelectorAll('[data-trends]').forEach(function (host) {
        var data = document.getElementById(host.dataset.trends);
        if (data) render(host, JSON.parse(data.textContent));
    });
})();
