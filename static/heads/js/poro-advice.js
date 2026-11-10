/* Poro AI recommends: the computed wording shows at once; each recommendation Poro writes replaces it once it passes
   the checks in heads/poro.py. The card is #adviceList with data-url, the page's poll address (School Head dashboard,
   teacher home); #poroSrc says where the wording comes from and #poroSwitch shows the other wording. */
(function () {
    'use strict';
    // ---------- Poro AI: the computed wording shows at once; each sentence Poro rewords replaces it once it passes ----------
    var advice = document.getElementById('adviceList'), poroSrc = document.getElementById('poroSrc'), poroSwitch = document.getElementById('poroSwitch');
    if (advice && advice.dataset.url) {
        var poroUrl = advice.dataset.url, tries = 0, showing = 'ai', fields = {};
        advice.querySelectorAll('[data-poro]').forEach(function (p) { fields[p.dataset.poro] = { el: p, rules: p.textContent, ai: null }; });
        var put = function (f, text) {
            if (f.el.textContent === text) return;
            f.el.classList.add('fading');
            setTimeout(function () { f.el.textContent = text; f.el.classList.remove('fading'); }, 250);
        };
        var apply = function (d) {
            Object.keys(d.fields || {}).forEach(function (k) {
                var f = fields[k];
                if (!f || f.ai === d.fields[k]) return;
                f.ai = d.fields[k];
                if (showing === 'ai') put(f, f.ai);
            });
        };
        var poll = function () {
            fetch(poroUrl, { headers: { 'X-Requested-With': 'XMLHttpRequest' } })
                .then(function (r) { return r.ok ? r.json() : null; })
                .then(function (d) {
                    if (d && d.state === 'writing') {
                        apply(d);
                        poroSrc.textContent = 'Poro AI is writing the advice' + (d.total ? ' (' + (d.done || 0) + ' of ' + d.total + ')' : '') + '…';
                        if (++tries <= 150) setTimeout(poll, 4000);
                        return;
                    }
                    if (d && d.source === 'ai') {
                        apply(d);
                        poroSrc.className = 'poro-src ai';
                        poroSrc.textContent = 'Written by Poro AI (' + d.model + ') for ' + d.written + ' of ' + d.total + ' recommendation' + (d.total === 1 ? '' : 's') + ', from the decisions; checked before it is shown';
                        poroSwitch.hidden = false;
                    } else {
                        poroSrc.textContent = d && d.unavailable ? 'Decided from the grades (Poro AI is not running)' : 'Decided from the grades by rules a person can check';
                    }
                })
                .catch(function () { poroSrc.textContent = 'Decided from the grades by rules a person can check'; });
        };
        poll();
        poroSwitch.addEventListener('click', function () {
            showing = showing === 'ai' ? 'rules' : 'ai';
            Object.keys(fields).forEach(function (k) { var f = fields[k]; put(f, showing === 'ai' && f.ai ? f.ai : f.rules); });
            poroSwitch.textContent = showing === 'ai' ? 'Show the computed wording' : "Show Poro AI's wording";
        });
    }
})();
