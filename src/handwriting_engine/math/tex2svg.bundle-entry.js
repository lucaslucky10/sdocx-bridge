// Source of tex2svg.bundle.js (npm run bundle). Same conversion as tex2svg.js, exposed as globalThis.tex2svg.
const {mathjax} = require('mathjax-full/js/mathjax.js');
const {TeX} = require('mathjax-full/js/input/tex.js');
const {SVG} = require('mathjax-full/js/output/svg.js');
const {liteAdaptor} = require('mathjax-full/js/adaptors/liteAdaptor.js');
const {RegisterHTMLHandler} = require('mathjax-full/js/handlers/html.js');
const {AllPackages} = require('mathjax-full/js/input/tex/AllPackages.js');
const adaptor = liteAdaptor();
RegisterHTMLHandler(adaptor);
const packages = AllPackages.filter(p => !['noerrors', 'noundefined'].includes(p));
const html = mathjax.document('', {InputJax: new TeX({packages}), OutputJax: new SVG({fontCache: 'none'})});
globalThis.tex2svg = function (tex, display) {
  return adaptor.innerHTML(html.convert(tex, {display: !!display}));
};
