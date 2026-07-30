// Thin fetch wrappers over the Flask API. No app logic here.
const Api = (() => {
  async function _json(resp) {
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.error || `Request failed (${resp.status})`);
    }
    return resp.json();
  }

  return {
    async upload(file) {
      const form = new FormData();
      form.append("zipfile", file);
      const resp = await fetch("/api/upload", { method: "POST", body: form });
      return _json(resp);
    },
    async discovery(analysisId) {
      return _json(await fetch(`/api/analyses/${analysisId}/discovery`));
    },
    async graph(analysisId) {
      return _json(await fetch(`/api/analyses/${analysisId}/graph`));
    },
    async webapp(analysisId, webappId) {
      return _json(await fetch(`/api/analyses/${analysisId}/webapps/${webappId}`));
    },
    async webappColumns(analysisId, webappId) {
      return _json(await fetch(`/api/analyses/${analysisId}/webapps/${webappId}/columns`));
    },
    async dataset(analysisId, datasetName) {
      return _json(await fetch(`/api/analyses/${analysisId}/datasets/${encodeURIComponent(datasetName)}`));
    },
    async inventory(analysisId) {
      return _json(await fetch(`/api/analyses/${analysisId}/inventory`));
    },
    async derivability(analysisId, webappId, sectionId) {
      return _json(
        await fetch(`/api/analyses/${analysisId}/webapps/${webappId}/sections/${sectionId}/derivability`)
      );
    },
  };
})();
