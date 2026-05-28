/* ============================================================
   biobert.js — Front-end wrapper for the BioBERT Flask API
   ============================================================ */

const BioBERT = {

    API_URL: 'http://localhost:5000',

    /**
     * Check whether the Python server is running.
     * Returns true if online, false otherwise.
     */
    async isOnline() {
        try {
            const res = await fetch(`${this.API_URL}/api/health`, { method: 'GET' });
            return res.ok;
        } catch {
            return false;
        }
    },

    /**
     * Send 'text' to BioBERT and get back an array of
     * { token: string, label: string, score: number, start: number, end: number } objects.
     *
     * Example:
     *   const entities = await BioBERT.analyzeText("Aspirin causes nausea.");
     *   // [{ token: "Aspirin", label: "Medication", score: 0.9912, start: 0, end: 7 }, ...]
     */
    async analyzeText(text) {
        const response = await fetch(`${this.API_URL}/api/ner`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });

        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || `BioBERT API error: HTTP ${response.status}`);
        }

        const data = await response.json();
        return data.entities; // [{ token, label, score, start, end }, ...]
    },

    /**
     * Filter the entity list to only medically interesting entity types.
     * The d4data/biomedical-ner-all model only returns real entities
     * (no "O" labels), so this filters by specific clinical categories.
     */
    filterMedicalEntities(entities) {
        const clinical = ['Medication', 'Drug', 'Disease_disorder', 'Sign_symptom',
                          'Clinical_event', 'Biological_structure', 'Diagnostic_procedure',
                          'Therapeutic_procedure'];
        return entities.filter(e => clinical.includes(e.label));
    },

    /**
     * Count how many entities per second the model processes.
     * This is used to update the "Live NLP Throughput" metric on the dashboard.
     */
    async measureThroughput(sampleText) {
        const start = performance.now();
        const entities = await this.analyzeText(sampleText);
        const elapsed = (performance.now() - start) / 1000; // seconds
        return Math.round(entities.length / elapsed);
    }

};
