// Typeset TeX to SVG with MathJax; glyphs stay as separate <path>s with data-c (codepoint) attributes.
const {mathjax} = require('mathjax-full/js/mathjax.js');
const {TeX} = require('mathjax-full/js/input/tex.js');
const {SVG} = require('mathjax-full/js/output/svg.js');
const {liteAdaptor} = require('mathjax-full/js/adaptors/liteAdaptor.js');
const {RegisterHTMLHandler} = require('mathjax-full/js/handlers/html.js');
const {AllPackages} = require('mathjax-full/js/input/tex/AllPackages.js');
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
// Keep TeX errors explicit; noerrors/noundefined turn failures into apparent text.
const packages = AllPackages.filter(p => !['noerrors', 'noundefined'].includes(p));
const html = mathjax.document('', {InputJax: new TeX({packages}), OutputJax: new SVG({fontCache: 'none'})});
const tex = process.argv[2];
const node = html.convert(tex, {display: process.argv[3] === 'display'});
console.log(adaptor.innerHTML(node));
