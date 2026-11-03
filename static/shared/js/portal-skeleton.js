/*
 * Every portal: skeleton screens while the next page loads.
 *
 * Pages arrive fully built from the server, so the wait is between a click and the next page. When
 * the user opens another page of the portal (a link, a row, a filter change), the current content gives
 * way to a grey outline of the page that is coming, so the screen answers at once. The new page
 * replaces it as soon as it arrives. A School Head filter change keeps the filter bar in place and
 * outlines the rest. If the browser brings this page back from its back/forward cache, the old content
 * returns. Downloads (exports, report cards, class records) never show an outline.
 *
 * The outlines are in templates/shared/_skeletons.html and heads/_skeletons.html.
 * Page scripts that navigate by themselves use ArmsSkeleton.go(url) and ArmsSkeleton.submit(form).
 */
(function () {
    'use strict';
    var DELAY = 120;            // a quick page never flashes an outline
    var GIVE_UP = 20000;        // a navigation that never happened (a stop, an error) gives the page back
    var PORTALS = /^\/(teachers|registrars|heads|admin-panel|admin|assessments)\//;
    // files, data and actions: they do not replace the page, so they get no outline
    var SKIP = /(\/export\/|\/download\/|\/report-cards?\/|\/e-class-record\/|\/mapeh-final-grades\/|\.csv$|\.xlsx$|\/api\/|\/data\/|\/read-all\/|\/status\/|\/email-preview\/|\/forecaster\/retrain\/)/;
    // pages with an outline of their own (heads/_skeletons.html)
    var ROUTES = [
        [/^\/heads\/(ai-dashboard\/)?$/, 'dashboard'],
        [/^\/heads\/ai-section\/\d+\/\d+\/\d+\/$/, 'dashboard'],
        [/^\/heads\/performance\/(\d+\/)?$/, 'performance'],
        [/^\/heads\/users\/$/, 'users'],
        [/^\/heads\/honors\/$/, 'p-dash'],
        [/^\/heads\/school-year\/$/, 'p-detail'],
        [/^\/accounts\/settings\/$/, 'p-settings'],
        [/^\/(teachers|registrars|admin-panel|admin)\/$/, 'p-dash'],
        [/\/(create|edit|upload|import|link)\/$/, 'p-form'],
        [/\/upload\/review\/$/, 'p-form'],
        [/\/notifications\/\d+\/open\/$/, 'p-generic'],
        [/\/\d+\/$/, 'p-detail']
    ];
    var wrap = document.querySelector('#mainContent .content-wrap');
    var timer = null, giveUp = null, active = null;

    function urlOf(href) {
        try { return new URL(href, location.href); } catch (e) { return null; }
    }
    function kindOf(url) {
        if (!url || url.origin !== location.origin || SKIP.test(url.pathname)) return null;
        var path = url.pathname;
        if (path !== '/accounts/settings/' && !PORTALS.test(path)) return null;
        for (var i = 0; i < ROUTES.length; i++) {
            if (ROUTES[i][0].test(path)) return ROUTES[i][1];
        }
        return 'p-list';
    }
    var here = kindOf(urlOf(location.href));

    // the sidebar shows where the user is going: the link whose address is the longest start of the new one
    function markNav(url) {
        var best = null, bestLen = 0;
        document.querySelectorAll('.sidebar .nav-menu:not(.nav-footer) .nav-link, .sidebar .nav-settings').forEach(function (a) {
            var u = urlOf(a.getAttribute('href') || '');
            if (!u || u.origin !== location.origin) return;
            var p = u.pathname;
            if (url.pathname.indexOf(p) === 0 && p.length > bestLen) { best = a; bestLen = p.length; }
        });
        if (!best) return;
        document.querySelectorAll('.sidebar .nav-link').forEach(function (a) { a.classList.toggle('active', a === best); });
    }

    function restore() {
        clearTimeout(timer); clearTimeout(giveUp);
        timer = giveUp = null;
        if (!active) return;
        active.node.remove();
        active.hidden.forEach(function (el) { el.classList.remove('skel-hide'); });
        active.nav.forEach(function (pair) { pair[0].classList.toggle('active', pair[1]); });
        document.body.removeAttribute('aria-busy');
        active = null;
    }

    function show(kind, keepFilters, url) {
        var tpl = document.getElementById('skel-' + kind) || document.getElementById('skel-p-generic');
        if (!tpl || !wrap || active) return;
        var node = tpl.content.firstElementChild.cloneNode(true);
        var hidden = [], keep = keepFilters ? document.querySelector('[data-skel-keep]') : null;
        var nav = Array.prototype.map.call(document.querySelectorAll('.sidebar .nav-link'), function (a) { return [a, a.classList.contains('active')]; });

        if (keep) {
            // same page, new filters: keep the filter bar, outline everything after it
            var filler = node.querySelector('[data-skel-filter]');
            if (filler) filler.remove();
            var root = keep.parentElement;
            Array.prototype.forEach.call(root.children, function (el) {
                if (el !== keep && !el.classList.contains('skel-hide')) { el.classList.add('skel-hide'); hidden.push(el); }
            });
            node.classList.add('skel-after');
            root.appendChild(node);
        } else {
            Array.prototype.forEach.call(wrap.children, function (el) {
                if (el.classList.contains('top-bar') || el.id === 'toastContainer' || el.classList.contains('skel-hide')) return;
                el.classList.add('skel-hide'); hidden.push(el);
            });
            wrap.appendChild(node);
            window.scrollTo(0, 0);
        }
        document.body.setAttribute('aria-busy', 'true');
        active = { node: node, hidden: hidden, nav: nav };
        if (url) markNav(url);
        giveUp = setTimeout(restore, GIVE_UP);
    }

    function schedule(url, keepFilters) {
        var kind = kindOf(url);
        if (!kind || active || timer) return;
        timer = setTimeout(function () { timer = null; show(kind, keepFilters, url); }, DELAY);
    }

    function forForm(form) {
        if ((form.getAttribute('method') || 'get').toLowerCase() !== 'get' || (form.target && form.target !== '_self')) return null;
        var url = urlOf(form.getAttribute('action') || location.pathname);
        return url ? { url: url, keep: kindOf(url) === here && url.pathname === location.pathname } : null;
    }

    // links (bubble phase, so page handlers that cancel a click are respected)
    document.addEventListener('click', function (e) {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var a = e.target.closest ? e.target.closest('a[href]') : null;
        if (!a || a.hasAttribute('download') || (a.target && a.target !== '_self')) return;
        var url = urlOf(a.getAttribute('href'));
        if (!url || (url.pathname === location.pathname && url.search === location.search)) return;
        schedule(url, false);
    });

    // filter forms sent with Enter or a button
    document.addEventListener('submit', function (e) {
        if (e.defaultPrevented) return;
        var t = forForm(e.target);
        if (t) schedule(t.url, t.keep);
    });

    // filter forms sent by a script (a select that applies on change) fire no submit event
    var nativeSubmit = HTMLFormElement.prototype.submit;
    HTMLFormElement.prototype.submit = function () {
        var t = forForm(this);
        if (t) schedule(t.url, t.keep);
        return nativeSubmit.apply(this, arguments);
    };

    // back/forward cache: the page comes back as it was
    window.addEventListener('pageshow', function (e) { if (e.persisted) restore(); });

    window.ArmsSkeleton = {
        go: function (href) {
            schedule(urlOf(href), false);
            window.location.href = href;
        },
        submit: function (form) { form.submit(); },
        show: function (kind, keepFilters) { show(kind, keepFilters); },
        restore: restore
    };
})();
