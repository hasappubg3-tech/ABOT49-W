/* ═══════════════════════════════════════════════════════════════
   شبكة الامير التعليمية — الجافاسكريبت الرئيسي
   ═══════════════════════════════════════════════════════════════ */

// ── الوضع الداكن ─────────────────────────────────────────────────
(function () {
  const html        = document.documentElement;
  const toggleBtn   = document.getElementById('theme-toggle');
  const sunIcon     = toggleBtn?.querySelector('.sun-icon');
  const moonIcon    = toggleBtn?.querySelector('.moon-icon');
  const STORAGE_KEY = 'alameer-theme';

  function applyTheme(theme) {
    html.setAttribute('data-theme', theme);
    if (sunIcon && moonIcon) {
      if (theme === 'dark') {
        sunIcon.classList.add('hidden');
        moonIcon.classList.remove('hidden');
      } else {
        sunIcon.classList.remove('hidden');
        moonIcon.classList.add('hidden');
      }
    }
  }

  // تحميل التفضيل المحفوظ، الافتراضي دائماً فاتح
  const saved = localStorage.getItem(STORAGE_KEY);
  applyTheme(saved || 'light');

  toggleBtn?.addEventListener('click', () => {
    const current = html.getAttribute('data-theme');
    const next    = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    localStorage.setItem(STORAGE_KEY, next);
  });
})();

// ── البحث الحي ───────────────────────────────────────────────────
(function () {
  const toggleBtn    = document.getElementById('search-toggle');
  const searchBar    = document.getElementById('search-bar');
  const searchInput  = document.getElementById('search-input');
  const clearBtn     = document.getElementById('clear-search');
  const resultsBox   = document.getElementById('search-results');

  if (!toggleBtn || !searchBar || !searchInput) return;

  // فتح/إغلاق شريط البحث
  toggleBtn.addEventListener('click', () => {
    const isOpen = searchBar.classList.toggle('open');
    if (isOpen) {
      searchInput.focus();
    } else {
      searchInput.value = '';
      clearResults();
    }
  });

  // زر مسح
  clearBtn?.addEventListener('click', () => {
    searchInput.value = '';
    searchInput.focus();
    clearResults();
    clearBtn.classList.add('hidden');
  });

  function clearResults() {
    if (resultsBox) {
      resultsBox.innerHTML = '';
      resultsBox.style.display = 'none';
    }
  }

  // Debounce
  let debounceTimer;
  searchInput.addEventListener('input', (e) => {
    const q = e.target.value.trim();
    clearBtn?.classList.toggle('hidden', !q);

    clearTimeout(debounceTimer);
    if (!q) { clearResults(); return; }

    debounceTimer = setTimeout(() => doSearch(q), 280);
  });

  // Enter → صفحة نتائج كاملة
  searchInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      const q = searchInput.value.trim();
      if (q) window.location.href = `/search?q=${encodeURIComponent(q)}`;
    }
    if (e.key === 'Escape') {
      searchBar.classList.remove('open');
      clearResults();
    }
  });

  async function doSearch(q) {
    try {
      const res  = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
      const data = await res.json();
      showResults(data, q);
    } catch { /* صامت */ }
  }

  function showResults(items, q) {
    if (!resultsBox) return;
    if (!items || items.length === 0) {
      resultsBox.innerHTML = `<div class="search-result-item" style="color:#888;text-align:center">لا توجد نتائج لـ "${escHtml(q)}"</div>`;
    } else {
      resultsBox.innerHTML = items.map(item =>
        `<a class="search-result-item" href="${escHtml(item.url)}">${escHtml(item.label)}</a>`
      ).join('') +
      (items.length >= 15
        ? `<a class="search-result-item" href="/search?q=${encodeURIComponent(q)}" style="color:var(--gold);text-align:center">عرض كل النتائج ←</a>`
        : '');
    }
    resultsBox.style.display = 'block';
  }

  // إغلاق عند الضغط خارج شريط البحث
  document.addEventListener('click', (e) => {
    if (!searchBar.contains(e.target) && e.target !== toggleBtn && !toggleBtn?.contains(e.target)) {
      searchBar.classList.remove('open');
      clearResults();
    }
  });
})();

// ── مشاركة الملزمة (من صفحة القائمة) ───────────────────────────
function shareNote(event, id, label) {
  event.preventDefault();
  event.stopPropagation();
  const url = `${window.location.origin}/note/${id}`;
  if (navigator.share) {
    navigator.share({ title: label, url });
  } else {
    navigator.clipboard.writeText(url).then(() => {
      showToastGlobal('تم نسخ الرابط ✓');
    });
  }
}

// Toast عالمي
function showToastGlobal(msg) {
  let t = document.getElementById('global-toast');
  if (!t) {
    t = document.createElement('div');
    t.id = 'global-toast';
    t.className = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2500);
}

// ── انتعاش الصور عند الخطأ ───────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('img').forEach(img => {
    img.addEventListener('error', function () {
      const parent = this.closest('.note-thumb, .gallery-item');
      if (parent && !this.dataset.errHandled) {
        this.dataset.errHandled = '1';
        this.style.display = 'none';
        // عرض placeholder نصي
        const letter = (this.alt || '📄')[0];
        const ph = document.createElement('div');
        ph.className = 'note-thumb-placeholder';
        ph.innerHTML = `<span>${letter}</span>`;
        parent.appendChild(ph);
      }
    });
  });
});

// ── مساعد HTML escape ─────────────────────────────────────────────
function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
