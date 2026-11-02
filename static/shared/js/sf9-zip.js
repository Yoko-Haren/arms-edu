/* Download all report cards: [data-sf9-zip="<url of the list>"] fetches each learner's SF9 and saves them as one zip, in a
   folder. The page builds the zip because a class of forty is larger than one server response can carry on Vercel.
   [data-sf9-section] reads the section from the form the button sits in. */
(function () {
    'use strict';
    var table = [];
    for (var n = 0; n < 256; n++) {
        var c = n;
        for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
        table[n] = c >>> 0;
    }
    function crc32(bytes) {
        var crc = 0xFFFFFFFF;
        for (var i = 0; i < bytes.length; i++) crc = table[(crc ^ bytes[i]) & 255] ^ (crc >>> 8);
        return (crc ^ 0xFFFFFFFF) >>> 0;
    }
    function header(size) {
        var bytes = new Uint8Array(size);
        return { bytes: bytes, view: new DataView(bytes.buffer) };
    }
    function zip(entries) {                 // stored, not compressed: each xlsx is compressed already
        var encoder = new TextEncoder(), parts = [], central = [], offset = 0, centralSize = 0;
        entries.forEach(function (entry) {
            var name = encoder.encode(entry.name), crc = crc32(entry.bytes), size = entry.bytes.length;
            var local = header(30), v = local.view;
            v.setUint32(0, 0x04034b50, true); v.setUint16(4, 20, true); v.setUint16(6, 0x0800, true);
            v.setUint32(14, crc, true); v.setUint32(18, size, true); v.setUint32(22, size, true); v.setUint16(26, name.length, true);
            parts.push(local.bytes, name, entry.bytes);
            var dir = header(46), d = dir.view;
            d.setUint32(0, 0x02014b50, true); d.setUint16(4, 20, true); d.setUint16(6, 20, true); d.setUint16(8, 0x0800, true);
            d.setUint32(16, crc, true); d.setUint32(20, size, true); d.setUint32(24, size, true); d.setUint16(28, name.length, true);
            d.setUint32(42, offset, true);
            central.push(dir.bytes, name);
            offset += 30 + name.length + size;
            centralSize += 46 + name.length;
        });
        var end = header(22), e = end.view;
        e.setUint32(0, 0x06054b50, true); e.setUint16(8, entries.length, true); e.setUint16(10, entries.length, true);
        e.setUint32(12, centralSize, true); e.setUint32(16, offset, true);
        return new Blob(parts.concat(central, [end.bytes]), { type: 'application/zip' });
    }
    function save(blob, name) {
        var link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(link.href); }, 10000);
    }
    document.addEventListener('click', async function (event) {
        var button = event.target.closest('[data-sf9-zip]');
        if (!button) return;
        var url = button.getAttribute('data-sf9-zip');
        if (button.hasAttribute('data-sf9-section')) {
            var section = button.closest('form').querySelector('[name="section"]');
            if (!section.value) { section.reportValidity(); return; }
            url += '?section=' + encodeURIComponent(section.value);
        }
        var label = button.innerHTML;
        button.disabled = true;
        try {
            var list = await (await fetch(url, { credentials: 'same-origin' })).json();
            var entries = [];
            for (var i = 0; i < list.files.length; i++) {
                button.textContent = 'Preparing ' + (i + 1) + ' of ' + list.files.length;
                var response = await fetch(list.files[i].url, { credentials: 'same-origin' });
                if (!response.ok) throw new Error(list.files[i].name);
                var number = String(i + 1).padStart(2, '0');
                entries.push({ name: list.folder + '/' + number + '_' + list.files[i].name, bytes: new Uint8Array(await response.arrayBuffer()) });
            }
            save(zip(entries), list.folder + '.zip');
        } catch (error) {
            window.alert('The report cards could not be downloaded. Check your connection and try again.');
        } finally {
            button.disabled = false;
            button.innerHTML = label;
        }
    });
})();
