/* ARMS landing page: header state, mobile menu, section highlighting, scroll-in effects, the forms chain, the portal tour and the photo viewer. */
(function () {
    'use strict';
    window.armsLanding = true;

    var root = document.documentElement;
    var header = document.getElementById('siteHeader');
    var nav = document.getElementById('siteNav');
    var menuBtn = header.querySelector('.menu-btn');
    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // The header turns solid once the page has scrolled, and the school rises a little slower than the page.
    var school = document.querySelector('.hero-school');
    var phone = window.matchMedia('(max-width: 559px)');
    function onScroll() {
        header.classList.toggle('is-scrolled', window.scrollY > 8);
        if (!school || reduceMotion || phone.matches) { if (school) school.style.transform = ''; return; }
        if (window.scrollY < window.innerHeight * 1.2) school.style.transform = 'translateY(' + Math.round(window.scrollY * 0.09) + 'px)';
    }
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });

    // Mobile menu.
    function setMenu(open) {
        header.classList.toggle('menu-open', open);
        menuBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        menuBtn.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
        menuBtn.querySelector('i').className = 'fi ' + (open ? 'fi-rr-cross' : 'fi-rr-menu-burger');
    }
    menuBtn.addEventListener('click', function () { setMenu(!header.classList.contains('menu-open')); });
    nav.addEventListener('click', function (e) { if (e.target.closest('a')) setMenu(false); });
    document.addEventListener('click', function (e) {
        if (header.classList.contains('menu-open') && !header.contains(e.target)) setMenu(false);
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && header.classList.contains('menu-open')) { setMenu(false); menuBtn.focus(); }
    });
    var wide = window.matchMedia('(min-width: 900px)');
    var closeOnWide = function () { if (wide.matches) setMenu(false); };
    if (wide.addEventListener) wide.addEventListener('change', closeOnWide); else wide.addListener(closeOnWide);

    // The forms chain: choosing a form lights up what it is made from.
    var chain = document.querySelector('[data-chain]');
    if (chain) {
        var forms = Array.prototype.slice.call(chain.querySelectorAll('[data-form]'));
        var sources = Array.prototype.slice.call(chain.querySelectorAll('[data-source]'));
        var pick = function (form) {
            var from = form.getAttribute('data-from').split(' ');
            forms.forEach(function (f) { f.setAttribute('aria-pressed', f === form ? 'true' : 'false'); });
            sources.forEach(function (s) { s.classList.toggle('is-on', from.indexOf(s.getAttribute('data-source')) !== -1); });
            chain.querySelector('[data-form-title]').textContent = form.getAttribute('data-title');
            chain.querySelector('[data-form-text]').textContent = form.getAttribute('data-text');
            chain.querySelector('[data-form-who]').textContent = form.getAttribute('data-who');
        };
        forms.forEach(function (form) {
            form.addEventListener('click', function () { pick(form); });
            form.addEventListener('mouseenter', function () { pick(form); });
            form.addEventListener('focus', function () { pick(form); });
        });
        pick(chain.querySelector('[data-form][aria-pressed="true"]'));
    }

    // The portal tour: tabs, with the arrow keys moving between them.
    var tour = document.querySelector('[data-tour]');
    if (tour) {
        var tabs = Array.prototype.slice.call(tour.querySelectorAll('[role="tab"]'));
        var select = function (tab, focus) {
            tabs.forEach(function (t) {
                var on = t === tab;
                t.setAttribute('aria-selected', on ? 'true' : 'false');
                t.tabIndex = on ? 0 : -1;
                document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
            });
            if (focus) tab.focus();
        };
        tabs.forEach(function (tab, index) {
            tab.addEventListener('click', function () { select(tab); });
            tab.addEventListener('keydown', function (e) {
                var step = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
                if (!step) return;
                e.preventDefault();
                select(tabs[(index + step + tabs.length) % tabs.length], true);
            });
        });
    }

    if (!('IntersectionObserver' in window)) {
        root.classList.remove('js');
    } else {
        // Highlight the menu link of the section in the middle of the screen.
        var links = {};
        Array.prototype.forEach.call(nav.querySelectorAll('a[href^="#"]'), function (a) { links[a.getAttribute('href').slice(1)] = a; });
        var spy = new IntersectionObserver(function (entries) {
            entries.forEach(function (entry) {
                if (!entry.isIntersecting) return;
                Object.keys(links).forEach(function (id) {
                    var on = id === entry.target.id;
                    links[id].classList.toggle('is-active', on);
                    if (on) links[id].setAttribute('aria-current', 'location'); else links[id].removeAttribute('aria-current');
                });
            });
        }, { rootMargin: '-45% 0px -50% 0px' });
        ['hero', 'forms', 'portals', 'insights', 'how', 'partner', 'contact'].forEach(function (id) {
            var section = document.getElementById(id);
            if (section) spy.observe(section);
        });

        // Fade sections in as they arrive; afterwards the element goes back to its normal styles.
        var reveals = document.querySelectorAll('[data-reveal]');
        function settle(el) {
            el.removeAttribute('data-reveal');
            el.classList.remove('is-in');
        }
        if (reduceMotion) {
            Array.prototype.forEach.call(reveals, settle);
        } else {
            var io = new IntersectionObserver(function (entries) {
                entries.forEach(function (entry) {
                    if (!entry.isIntersecting) return;
                    var el = entry.target;
                    io.unobserve(el);
                    el.addEventListener('transitionend', function done(e) {
                        if (e.target !== el || e.propertyName !== 'opacity') return;
                        el.removeEventListener('transitionend', done);
                        settle(el);
                    });
                    el.classList.add('is-in');
                });
            }, { rootMargin: '0px 0px -8% 0px', threshold: 0.1 });
            Array.prototype.forEach.call(reveals, function (el) { io.observe(el); });
        }
    }

    // Photo viewer.
    var viewer = document.getElementById('lightbox');
    var photos = Array.prototype.slice.call(document.querySelectorAll('[data-photo]'));
    if (!viewer || typeof viewer.showModal !== 'function' || !photos.length) return;
    var viewerImg = viewer.querySelector('img');
    var caption = viewer.querySelector('.lb-caption');
    var count = viewer.querySelector('.lb-count');
    var current = 0;
    var opener = null;

    function show(index) {
        current = (index + photos.length) % photos.length;
        var link = photos[current];
        viewerImg.src = link.getAttribute('href');
        viewerImg.alt = link.querySelector('img').alt;
        caption.textContent = link.getAttribute('data-caption');
        count.textContent = (current + 1) + ' of ' + photos.length;
    }
    photos.forEach(function (link, index) {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            opener = link;
            show(index);
            viewer.showModal();
            root.classList.add('lb-open');
        });
    });
    // Every way out (button, backdrop, Escape) unlocks the page and puts focus back on the photo that was opened.
    function unlock() { root.classList.remove('lb-open'); }
    function restoreFocus() {
        if (!opener) return;
        var link = opener;
        opener = null;
        link.focus();
    }
    function closeViewer() {
        if (viewer.open) viewer.close();
        unlock();
        restoreFocus();
    }
    viewer.querySelector('.lb-prev').addEventListener('click', function () { show(current - 1); });
    viewer.querySelector('.lb-next').addEventListener('click', function () { show(current + 1); });
    viewer.querySelector('.lb-close').addEventListener('click', closeViewer);
    viewer.addEventListener('click', function (e) { if (e.target === viewer) closeViewer(); });
    viewer.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowLeft') { e.preventDefault(); show(current - 1); }
        if (e.key === 'ArrowRight') { e.preventDefault(); show(current + 1); }
    });
    // Escape: the dialog is still open during "cancel", so only unlock here; focus returns on "close".
    viewer.addEventListener('cancel', unlock);
    viewer.addEventListener('close', function () { unlock(); restoreFocus(); });
})();
