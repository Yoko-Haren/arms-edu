/*
 * Forecast charts: the School Head's Academic Dashboard and School Performance, and the teacher home.
 * Two solid lines only: Actual Performance (blue, the recorded averages, filled dots) and Predicted Performance
 * (green, the predictive model's averages, solid dots like the actual line). The grade axis is zoomed to the values shown.
 *
 * The server builds one spec per chart (heads/forecasts.py) into a JSON script tag; an element with
 * data-forecast="<id of that tag>" becomes the chart. Each chart has two lines over the school year's
 * grading periods: the actual grade (blue) and the predicted grade (green). The predicted line
 * is what the grade model forecast for each period, one period ahead (heads/forecasts.py "back"), joined to
 * its forecast for the periods still to come; without a trained model it starts at the latest actual grade.
 * "Show" picks the series (the overall average first). Pointing at a period, or moving to it with the
 * keyboard, shows both values with their colors. The Table view lists every value.
 */
(function () {
    'use strict';
    var SVG = 'http://www.w3.org/2000/svg';
    var ACTUAL = '#2563eb', PREDICTED = '#16a34a';
    var LINE_H = 300;

    function remember(key, value) {
        try {
            if (value === undefined) return localStorage.getItem(key);
            localStorage.setItem(key, value);
        } catch (e) { /* private mode: the default view is fine */ }
        return null;
    }
    function el(name, attrs, parent) {
        var node = document.createElementNS(SVG, name);
        Object.keys(attrs || {}).forEach(function (k) { if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, attrs[k]); });
        if (parent) parent.appendChild(node);
        return node;
    }
    function tag(name, cls, parent, text) {
        var node = document.createElement(name);
        if (cls) node.className = cls;
        if (text !== undefined && text !== null) node.textContent = text;
        if (parent) parent.appendChild(node);
        return node;
    }
    function fmt(v) { return v === null || v === undefined ? '—' : (+v).toFixed(1); }
    function lineKey(parent, color, dashed) {
        var k = tag('i', 'fc-key-line' + (dashed ? ' dash' : ''), parent);
        k.style.borderTopColor = color;
        k.setAttribute('aria-hidden', 'true');
        return k;
    }

    // ---------- one series, period by period: the actual grade and the predicted grade ----------
    function points(spec, se) {
        var pts = spec.periods.map(function (p) {
            var f = se.future[p.p];
            var actual = se.actual[p.p] === undefined ? null : se.actual[p.p];
            var predicted = f ? f[0] : (se.back[p.p] === undefined ? null : se.back[p.p]);
            return { actual: actual, predicted: predicted, join: false };
        });
        // without the model's earlier forecasts the predicted line would be a lone point: start it at the latest actual grade
        var last = -1;
        pts.forEach(function (pt, i) { if (pt.actual !== null) last = i; });
        var before = pts.slice(0, last + 1).some(function (pt) { return pt.predicted !== null; });
        if (last >= 0 && !before && pts.slice(last + 1).some(function (pt) { return pt.predicted !== null; })) {
            pts[last].predicted = pts[last].actual;
            pts[last].join = true;
        }
        return pts;
    }

    // the grade axis hugs the values shown, so a point or two of change is visible
    function domain(pts) {
        var values = [];
        pts.forEach(function (pt) { [pt.actual, pt.predicted].forEach(function (v) { if (v !== null) values.push(v); }); });
        if (!values.length) values = [75];
        var lo = Math.max(60, Math.floor(Math.min.apply(null, values) - 2)), hi = Math.min(100, Math.ceil(Math.max.apply(null, values) + 2));
        if (hi - lo < 8) { var mid = (lo + hi) / 2; lo = Math.max(60, Math.floor(mid - 4)); hi = Math.min(100, lo + 8); }
        return { lo: lo, hi: hi };
    }

    function pathOf(coords) {
        return coords.map(function (c, i) { return (i ? 'L' : 'M') + c[0].toFixed(1) + ',' + c[1].toFixed(1); }).join(' ');
    }

    // ---------- the tooltip: both values at one period ----------
    function showTip(host, tip, spec, se, pts, i, anchorX, anchorY) {
        tip.textContent = '';
        var pt = pts[i];
        tag('h5', '', tip, spec.periods[i].name + (pt.actual === null ? ' (forecast)' : ''));
        [['Actual grades', pt.actual, ACTUAL, false], ['Forecasted grades', pt.join ? null : pt.predicted, PREDICTED, false]].forEach(function (r) {
            var row = tag('div', 'fc-tip-row', tip);
            lineKey(row, r[2], r[3]);
            tag('b', '', row, r[1] === null ? '—' : fmt(r[1]) + '%');
            tag('span', '', row, r[0]);
        });
        tip.hidden = false;
        var box = host.getBoundingClientRect(), w = tip.offsetWidth, h = tip.offsetHeight;
        var left = anchorX - box.left + 14;
        if (left + w > box.width - 4) left = anchorX - box.left - w - 14;
        var top = Math.min(Math.max(anchorY - box.top - h / 2, 4), box.height - h - 4);
        tip.style.left = Math.max(4, left) + 'px';
        tip.style.top = top + 'px';
    }

    // ---------- the line view ----------
    function drawLines(host, box, tip, spec, se) {
        var pts = points(spec, se);
        var W = Math.max(260, Math.floor(box.clientWidth)), H = LINE_H;
        var m = { top: 14, right: 24, bottom: 48, left: 52 };
        var iw = W - m.left - m.right, ih = H - m.top - m.bottom;
        var d = domain(pts);
        var n = spec.periods.length;
        var x = function (i) { return m.left + (n === 1 ? iw / 2 : (i * iw) / (n - 1)); };
        var y = function (v) { return m.top + ih - ((v - d.lo) / (d.hi - d.lo)) * ih; };
        var svg = el('svg', { class: 'fc-svg', viewBox: '0 0 ' + W + ' ' + H, width: W, height: H, role: 'img',
                              'aria-label': se.name + ': actual and predicted grades by grading period' }, box);

        var selected = spec.periods.map(function (p) { return p.p; }).indexOf(spec.selected);
        var step = d.hi - d.lo > 16 ? 5 : 2;
        for (var v = Math.ceil(d.lo / step) * step; v <= d.hi; v += step) {
            el('line', { class: 'fc-grid', x1: m.left, x2: m.left + iw, y1: y(v), y2: y(v) }, svg);
            el('text', { class: 'fc-tick', x: m.left - 8, y: y(v) + 4, 'text-anchor': 'end' }, svg).textContent = v;
        }
        var narrow = iw / Math.max(1, n - 1) < 90;
        spec.periods.forEach(function (p, i) {
            el('text', { class: 'fc-xlabel' + (i === selected ? ' is-selected' : ''), x: x(i), y: m.top + ih + 18, 'text-anchor': 'middle' }, svg)
                .textContent = narrow ? p.label : p.name;
        });
        el('text', { class: 'fc-axis-title', x: m.left + iw / 2, y: H - 6, 'text-anchor': 'middle' }, svg).textContent = 'Grading period';
        el('text', { class: 'fc-axis-title', x: 14, y: m.top + ih / 2, 'text-anchor': 'middle', transform: 'rotate(-90 14 ' + (m.top + ih / 2) + ')' }, svg)
            .textContent = 'Grades (%)';

        var actual = [], predicted = [];
        pts.forEach(function (pt, i) {
            if (pt.actual !== null) actual.push([x(i), y(pt.actual), i]);
            if (pt.predicted !== null) predicted.push([x(i), y(pt.predicted), i, pt.join]);
        });
        if (predicted.length > 1) el('path', { class: 'fc-line', d: pathOf(predicted), stroke: PREDICTED }, svg);
        if (actual.length > 1) el('path', { class: 'fc-line', d: pathOf(actual), stroke: ACTUAL }, svg);
        predicted.forEach(function (c) { if (!c[3]) el('circle', { class: 'fc-dot', cx: c[0], cy: c[1], r: 4.5, fill: PREDICTED }, svg); });
        actual.forEach(function (c) { el('circle', { class: 'fc-dot', cx: c[0], cy: c[1], r: 4.5, fill: ACTUAL }, svg); });

        // pointing at a period (or moving to it with the keyboard) shows both values
        var cross = el('line', { class: 'fc-cross', y1: m.top, y2: m.top + ih, visibility: 'hidden' }, svg);
        var hitW = n > 1 ? iw / (n - 1) : iw;
        spec.periods.forEach(function (p, i) {
            var hit = el('rect', { class: 'fc-hit', x: x(i) - hitW / 2, y: m.top, width: hitW, height: ih, tabindex: 0,
                                   'aria-label': p.name + ': actual ' + fmt(pts[i].actual) + ', predicted ' + fmt(pts[i].join ? null : pts[i].predicted) }, svg);
            var on = function (e) {
                cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
                var r = svg.getBoundingClientRect(), scale = r.width / W;
                var value = pts[i].actual !== null ? pts[i].actual : pts[i].predicted;
                var ax = e && e.clientX ? e.clientX : r.left + x(i) * scale;
                var ay = value !== null ? r.top + y(value) * scale : r.top + (m.top + ih / 2) * scale;
                showTip(host, tip, spec, se, pts, i, ax, ay);
            };
            var off = function () { cross.setAttribute('visibility', 'hidden'); tip.hidden = true; };
            hit.addEventListener('mouseenter', on);
            hit.addEventListener('mousemove', on);
            hit.addEventListener('mouseleave', off);
            hit.addEventListener('focus', function () { on(null); });
            hit.addEventListener('blur', off);
        });
    }

    // ---------- the table view ----------
    function drawTable(box, spec, se) {
        var pts = points(spec, se);
        var wrap = tag('div', 'fc-table-wrap', box);
        var table = tag('table', 'fc-table', wrap);
        var head = tag('tr', '', tag('thead', '', table));
        ['Grading period', 'Actual grades', 'Forecasted grades'].forEach(function (t, i) { tag('th', i ? 'num' : '', head, t); });
        var body = tag('tbody', '', table);
        spec.periods.forEach(function (p, i) {
            var tr = tag('tr', '', body);
            tag('th', '', tr, p.name);
            tag('td', 'num', tr, pts[i].actual === null ? '—' : fmt(pts[i].actual) + '%');
            tag('td', 'num' + (pts[i].actual === null && pts[i].predicted !== null ? ' is-fc' : ''), tr,
                pts[i].join || pts[i].predicted === null ? '—' : fmt(pts[i].predicted) + '%');
        });
    }

    // ---------- one chart: toolbar, legend, view ----------
    function mount(host) {
        var source = document.getElementById(host.dataset.forecast);
        if (!source) return;
        var spec = JSON.parse(source.textContent);
        spec.levels = (spec.levels || []).slice().sort(function (a, b) { return b.min - a.min; });
        host.classList.add('fc');
        host.textContent = '';
        if (!spec.series.length) { tag('p', 'fc-empty', host, 'No grades yet for this selection, so there is nothing to forecast.'); return; }
        var id = host.dataset.forecast;
        var ref = spec.series.filter(function (se) { return se.reference; })[0] || null;
        var groups = spec.series.filter(function (se) { return !se.reference; });
        var state = { view: remember('arms-fc-view:' + id) || 'line', pick: remember('arms-fc-pick:' + id) };
        if (['line', 'table'].indexOf(state.view) < 0) state.view = 'line';
        if (!spec.series.some(function (se) { return se.key === state.pick; })) state.pick = (ref || spec.series[0]).key;

        var bar = tag('div', 'fc-toolbar', host);
        var tabs = tag('div', 'fc-tabs', bar);
        tabs.setAttribute('role', 'tablist');
        tabs.setAttribute('aria-label', 'Show the forecast as');
        var stageId = id + '-view';
        var buttons = [['line', 'Line', 'fi-rr-chart-line-up'], ['table', 'Table', 'fi-rr-table-list']].map(function (v) {
            var b = tag('button', '', tabs);
            b.type = 'button';
            b.setAttribute('role', 'tab');
            b.setAttribute('aria-controls', stageId);
            tag('i', 'fi ' + v[2], b).setAttribute('aria-hidden', 'true');
            tag('span', '', b, v[1]);
            b.addEventListener('click', function () { state.view = v[0]; remember('arms-fc-view:' + id, v[0]); render(); });
            b.dataset.view = v[0];
            return b;
        });
        if (spec.series.length > 1) {
            var pick = tag('label', 'fc-period fc-pick', bar);
            tag('span', '', pick, 'Show');
            var select = tag('select', '', pick);
            if (ref) tag('option', '', select, ref.name + ' (overall)').value = ref.key;
            groups.forEach(function (se) { tag('option', '', select, se.name).value = se.key; });
            select.value = state.pick;
            select.addEventListener('change', function () { state.pick = select.value; remember('arms-fc-pick:' + id, state.pick); render(); });
        }

        var legend = tag('div', 'fc-legend fc-legend-two', host);
        [['Actual Performance', ACTUAL, false], ['Predicted Performance', PREDICTED, false]].forEach(function (r) {
            var item = tag('span', 'fc-style', legend);
            lineKey(item, r[1], r[2]);
            tag('span', '', item, r[0]);
        });
        var stage = tag('div', 'fc-stage', host);
        stage.id = stageId;
        stage.setAttribute('role', 'tabpanel');
        var tip = tag('div', 'fc-tip', host);
        tip.hidden = true;
        tip.setAttribute('aria-hidden', 'true');
        if (spec.method && spec.method.text) tag('p', 'fc-method explain', host, spec.method.text);

        function render() {
            var se = spec.series.filter(function (s) { return s.key === state.pick; })[0] || spec.series[0];
            buttons.forEach(function (b) { b.setAttribute('aria-selected', b.dataset.view === state.view ? 'true' : 'false'); });
            legend.hidden = state.view !== 'line';
            stage.textContent = '';
            tip.hidden = true;
            if (state.view === 'table') drawTable(stage, spec, se);
            else drawLines(host, stage, tip, spec, se);
        }
        render();

        var width = host.clientWidth, pending = false;
        var onResize = function () {
            if (pending) return;
            pending = true;
            requestAnimationFrame(function () {
                pending = false;
                if (Math.abs(host.clientWidth - width) > 4) { width = host.clientWidth; render(); }
            });
        };
        if (window.ResizeObserver) new ResizeObserver(onResize).observe(host);
        else window.addEventListener('resize', onResize);
    }

    function mountAll() { document.querySelectorAll('[data-forecast]').forEach(mount); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mountAll);
    else mountAll();
    window.ArmsForecast = { mount: mount };
})();
