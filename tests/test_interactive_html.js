const fs = require('fs');
const html = fs.readFileSync('frontend/index.html', 'utf8');

// Mock window and document environment
let downloadedFiles = [];

const mockElement = {
    innerText: '',
    innerHTML: '',
    value: '',
    getContext: () => ({
        clearRect: () => {},
        strokeRect: () => {},
        beginPath: () => {},
        moveTo: () => {},
        lineTo: () => {},
        stroke: () => {},
        fill: () => {},
        arc: () => {},
        fillRect: () => {},
        setLineDash: () => {}
    }),
    appendChild: () => {},
    classList: { remove: () => {}, add: () => {} },
    addEventListener: () => {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 500, height: 500 }),
    style: {}
};

const fakeDoc = {
    getElementById: (id) => mockElement,
    createElement: (tag) => {
        const el = Object.assign({}, mockElement);
        el.download = '';
        el.href = '';
        el.click = () => {
            downloadedFiles.push(el.download);
        };
        return el;
    },
    body: {
        appendChild: () => {},
        removeChild: () => {}
    }
};

const scriptMatches = html.match(/<script[\s\S]*?<\/script>/gi);
const mainScript = scriptMatches.find(s => s.includes('async function exportInteractiveReport'));
const code = mainScript.replace(/<script[^>]*>/i, '').replace(/<\/script>/i, '');

const vm = require('vm');
const context = vm.createContext({
    window: {
        addEventListener: () => {}
    },
    document: fakeDoc,
    Blob: function(content, options) {
        this.content = content;
        this.options = options;
        this.size = content ? content.length : 0;
    },
    URL: {
        createObjectURL: (blob) => 'blob:mock-url-' + blob.size,
        revokeObjectURL: () => {}
    },
    alert: (msg) => console.log('ALERT:', msg),
    console: console,
    Math: Math,
    Date: Date,
    JSON: JSON,
    Number: Number,
    parseFloat: parseFloat,
    parseInt: parseInt,
    setTimeout: (fn) => fn(),
    clearTimeout: () => {},
    requestAnimationFrame: () => {},
    cancelAnimationFrame: () => {}
});

vm.runInContext(code, context);

async function run() {
    console.log('Testing Simulation Studio export...');
    vm.runInContext(`
        appState.simData = {
            trajectory: [
                { points: [[0.1, 0.2], [0.3, 0.4]], total_energy: 1.5, gamma_hat: 0.98, min_spacing: 0.045, cv_nnd: 0.28 },
                { points: [[0.15, 0.25], [0.35, 0.45]], total_energy: 0.8, gamma_hat: 1.02, min_spacing: 0.044, cv_nnd: 0.27 }
            ],
            results: { measured_gamma_hat: 1.01, min_spacing: 0.044, cv_nnd: 0.27 },
            frequencies: [1, 2, 3, 4],
            radial_psd: [0.1, 0.5, 0.9, 1.0]
        };
        appState.targetGamma = '1.0'; // string test!
    `, context);

    await vm.runInContext(`exportInteractiveReport('sim')`, context);

    console.log('Testing Universal Studio export...');
    vm.runInContext(`
        univState.trajectory = [
            { points: [[0.2, 0.3], [0.4, 0.5]], loss: 0.05, min_spacing: 0.042 },
            { points: [[0.22, 0.32], [0.42, 0.52]], loss: 0.003, min_spacing: 0.041 }
        ];
        univState.targetMode = 'shape';
        univState.targetShape = 'heart';
        appState.univData = {
            results: { min_spacing: 0.041, cv_nnd: 0.26 },
            frequencies: [1, 2, 3],
            radial_psd: [0.2, 0.6, 0.8]
        };
    `, context);

    await vm.runInContext(`exportInteractiveReport('univ')`, context);

    console.log('Downloaded Files:', downloadedFiles);
    if (downloadedFiles.length === 2) {
        console.log('SUCCESS! Both interactive HTML dossiers generated and downloaded cleanly.');
    } else {
        throw new Error('Expected 2 downloads, got ' + downloadedFiles.length);
    }
}

run().catch(err => {
    console.error('Test failed:', err);
    process.exit(1);
});
