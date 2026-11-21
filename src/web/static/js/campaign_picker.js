// Search the loaded campaign catalogue; only an explicit choice changes the editor.
class CampaignPicker {
  constructor({ select, input, dropdown }) {
    this.select = select;
    this.input = input;
    this.dropdown = dropdown;
    this.results = [];
    this.highlightedIndex = -1;
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-controls', dropdown.id);
    input.setAttribute('aria-expanded', 'false');
    dropdown.setAttribute('role', 'listbox');

    input.addEventListener('focus', () => {
      input.select();
      this.search('');
    });
    input.addEventListener('click', () => {
      if (dropdown.hidden) {
        input.select();
        this.search('');
      }
    });
    input.addEventListener('input', () => this.search(input.value));
    input.addEventListener('keydown', (event) => this.onKeyDown(event));
    input.addEventListener('blur', () => this.close());
    document.addEventListener('pointerdown', (event) => {
      if (!input.contains(event.target) && !dropdown.contains(event.target)) this.close();
    });
  }

  sync() {
    const selected = Array.from(this.select.options).find(option => option.value === this.select.value);
    this.input.value = selected && selected.value ? selected.textContent : '';
  }

  search(query) {
    const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    this.results = Array.from(this.select.options).filter(option =>
      (!words.length || option.value) &&
      words.every(word => option.textContent.toLocaleLowerCase().includes(word))
    );
    this.highlightedIndex = Math.max(0, this.results.findIndex(option => option.value === this.select.value));
    this.dropdown.replaceChildren();
    this.results.forEach((option, index) => {
      const row = document.createElement('div');
      row.id = `${this.dropdown.id}-option-${index}`;
      row.className = 'dropdown-item';
      row.textContent = option.textContent;
      row.setAttribute('role', 'option');
      row.addEventListener('pointerdown', event => event.preventDefault());
      row.addEventListener('click', () => this.choose(index));
      row.addEventListener('pointerenter', () => this.highlight(index));
      this.dropdown.append(row);
    });
    if (!this.results.length) {
      const status = document.createElement('div');
      status.className = 'dropdown-status';
      status.setAttribute('role', 'status');
      status.textContent = 'Кампании не найдены';
      this.dropdown.append(status);
    }
    this.dropdown.hidden = false;
    this.input.setAttribute('aria-expanded', 'true');
    this.highlight(this.highlightedIndex);
  }

  highlight(index) {
    this.highlightedIndex = index;
    this.input.removeAttribute('aria-activedescendant');
    Array.from(this.dropdown.children).forEach((row, position) => {
      if (!this.results[position]) return;
      const active = position === index;
      row.classList.toggle('highlighted', active);
      row.setAttribute('aria-selected', String(active));
      if (active) {
        this.input.setAttribute('aria-activedescendant', row.id);
        row.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  choose(index) {
    const option = this.results[index];
    if (!option) return;
    const changed = this.select.value !== option.value;
    this.select.value = option.value;
    this.close();
    if (changed) this.select.dispatchEvent(new Event('change', { bubbles: true }));
  }

  close() {
    this.dropdown.hidden = true;
    this.input.setAttribute('aria-expanded', 'false');
    this.input.removeAttribute('aria-activedescendant');
    this.sync();
  }

  onKeyDown(event) {
    if (event.isComposing) return;
    if (event.key === 'Escape' || event.key === 'Tab') {
      if (event.key === 'Escape' && !this.dropdown.hidden) event.preventDefault();
      this.close();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (this.dropdown.hidden) {
        this.search('');
      } else if (this.results.length) {
        const delta = event.key === 'ArrowDown' ? 1 : -1;
        this.highlight((this.highlightedIndex + delta + this.results.length) % this.results.length);
      }
    } else if (event.key === 'Enter' && !this.dropdown.hidden) {
      event.preventDefault();
      this.choose(this.highlightedIndex);
    }
  }
}

window.CampaignPicker = CampaignPicker;
