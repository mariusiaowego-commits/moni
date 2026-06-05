// CRUD: delete with two-step confirmation
// Access parent Alpine data via DOM
function getApp() {
  return document.querySelector('#app').__x.$data;
}

document.addEventListener('alpine:init', () => {
  Alpine.data('crud', () => ({
    loadCrudArticles() {
      const app = getApp();
      fetch('/api/articles', { credentials: 'same-origin' })
        .then(r => r.json())
        .then(data => {
          app.crudArticles = Array.isArray(data) ? data : (data.articles || []);
        })
        .catch(err => console.error('Failed to load articles:', err));
    },

    showDeleteModal(article) {
      const app = getApp();
      app.deleteTarget = article;
      app.showModal = true;
    },

    closeModal() {
      const app = getApp();
      app.showModal = false;
      app.deleteTarget = null;
    },

    confirmDelete() {
      const app = getApp();
      const article = app.deleteTarget;
      if (!article || !article.id) return;

      fetch(`/api/articles/${article.id}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      })
        .then(r => {
          if (!r.ok) throw new Error('Delete failed');
          // Remove from CRUD list
          app.crudArticles = app.crudArticles.filter(a => a.id !== article.id);
          // Also remove from other lists
          if (app.articles) {
            app.articles = app.articles.filter(a => a.id !== article.id);
          }
          if (app.allArticles) {
            app.allArticles = app.allArticles.filter(a => a.id !== article.id);
          }
          this.closeModal();
        })
        .catch(err => {
          console.error('Delete failed:', err);
          alert('删除失败: ' + err.message);
          this.closeModal();
        });
    },
  }));
});

// Auto-load when navigate to crud view - handled in app.js via currentView watcher
