import { useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import hljs from 'highlight.js/lib/core';
import { buildComparisonRows } from '../utils/codeComparison';
import 'highlight.js/styles/github-dark.css';

const LANGUAGE_LOADERS = {
  python: () => import('highlight.js/lib/languages/python'),
  javascript: () => import('highlight.js/lib/languages/javascript'),
  typescript: () => import('highlight.js/lib/languages/typescript'),
  java: () => import('highlight.js/lib/languages/java'),
  csharp: () => import('highlight.js/lib/languages/csharp'),
  cpp: () => import('highlight.js/lib/languages/cpp'),
  go: () => import('highlight.js/lib/languages/go'),
  rust: () => import('highlight.js/lib/languages/rust'),
  ruby: () => import('highlight.js/lib/languages/ruby'),
  php: () => import('highlight.js/lib/languages/php'),
  swift: () => import('highlight.js/lib/languages/swift'),
  kotlin: () => import('highlight.js/lib/languages/kotlin'),
};

const LANGUAGE_ALIASES = {
  c: 'cpp',
  'c++': 'cpp',
  'c#': 'csharp',
  py: 'python',
};

function highlightLine(line, language) {
  if (line === undefined) return '';
  const selected = LANGUAGE_ALIASES[language.toLowerCase()] ?? language.toLowerCase();
  if (!hljs.getLanguage(selected)) return null;
  return hljs.highlight(line || ' ', { language: selected, ignoreIllegals: true }).value;
}

function CodeLine({ line, number, language, kind }) {
  const highlighted = highlightLine(line, language);
  return (
    <div className={`comparison-line comparison-line-${kind}`}>
      <span className="comparison-line-number">{number ?? ''}</span>
      {line === undefined ? (
        <span className="comparison-empty" aria-hidden="true">&nbsp;</span>
      ) : highlighted === null ? (
        <span>{line || ' '}</span>
      ) : (
        <span dangerouslySetInnerHTML={{ __html: highlighted }} />
      )}
    </div>
  );
}

CodeLine.propTypes = {
  line: PropTypes.string,
  number: PropTypes.number,
  language: PropTypes.string.isRequired,
  kind: PropTypes.string.isRequired,
};

export default function CodeComparison({
  original,
  proposed,
  language,
  startLine = 1,
  onApply,
  applyLabel = 'Apply proposal to editor',
}) {
  const rows = buildComparisonRows(original, proposed, startLine);
  const [copyError, setCopyError] = useState('');
  const [highlightError, setHighlightError] = useState('');
  const selectedLanguage = LANGUAGE_ALIASES[language.toLowerCase()] ?? language.toLowerCase();

  useEffect(() => {
    let active = true;
    const loader = LANGUAGE_LOADERS[selectedLanguage];
    setHighlightError('');
    if (!loader || hljs.getLanguage(selectedLanguage)) return () => { active = false; };

    loader()
      .then(({ default: grammar }) => {
        if (active) {
          hljs.registerLanguage(selectedLanguage, grammar);
          setHighlightError(null);
        }
      })
      .catch(() => {
        if (active) setHighlightError('Syntax highlighting could not be loaded; the code remains available as plain text.');
      });

    return () => { active = false; };
  }, [selectedLanguage]);

  const copy = async (value) => {
    setCopyError('');
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      setCopyError('Clipboard access was denied by this browser.');
    }
  };

  return (
    <section className="code-comparison" aria-label="Original and proposed code comparison">
      <div className="comparison-panes">
        {[
          { key: 'original', title: 'Current source', code: original, side: 'original' },
          { key: 'proposed', title: 'Proposed correction', code: proposed, side: 'proposed' },
        ].map((pane) => (
          <div className="comparison-pane" key={pane.key}>
            <header className="comparison-pane-header">
              <span>{pane.title}</span>
              <button
                type="button"
                className="btn btn-sm btn-outline-light"
                onClick={() => copy(pane.code)}
                aria-label={`Copy ${pane.title.toLowerCase()}`}
              >
                <i className="bi bi-clipboard me-1" />Copy
              </button>
            </header>
            <div className="comparison-code" role="region" aria-label={`${pane.title} lines`}>
              {rows.map((row, index) => (
                <CodeLine
                  key={`${pane.key}-${index}`}
                  line={row[pane.side]}
                  number={row[`${pane.side}Number`]}
                  language={language}
                  kind={row.kind}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
      {onApply && (
        <button type="button" className="btn btn-success mt-3" onClick={() => onApply(proposed)}>
          <i className="bi bi-check2-circle me-2" />{applyLabel}
        </button>
      )}
      {highlightError && <p className="small text-warning mt-2 mb-0" role="status">{highlightError}</p>}
      {copyError && <p className="small text-danger mt-2 mb-0" role="status">{copyError}</p>}
    </section>
  );
}

CodeComparison.propTypes = {
  original: PropTypes.string.isRequired,
  proposed: PropTypes.string.isRequired,
  language: PropTypes.string.isRequired,
  startLine: PropTypes.number,
  onApply: PropTypes.func,
  applyLabel: PropTypes.string,
};
