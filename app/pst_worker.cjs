'use strict';

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const VENDOR_PST = path.join(__dirname, 'vendor', 'pst_node', 'node_modules', 'pst-extractor');
const { PSTFile } = require(VENDOR_PST);

function sha256(bufOrStr) {
  return crypto.createHash('sha256').update(bufOrStr).digest('hex');
}

function safeName(s, fallback = 'item') {
  let clean = String(s || '')
    .replace(/[\x00-\x1f/\\:*?"<>|]/g, '_')
    .replace(/[. ]+$/g, '')
    .trim();
  if (!clean) clean = fallback;
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)/i.test(clean)) {
    clean = '_' + clean;
  }
  return clean.slice(0, 90);
}

function safeFileName(name, fallback = 'attachment') {
  const raw = String(name || '').replace(/[\x00-\x1f/\\:*?"<>|]/g, '_').trim();
  const ext = path.extname(raw);
  const base = ext ? raw.slice(0, raw.length - ext.length) : raw;
  let cleanBase = base.replace(/[. ]+$/g, '').trim() || fallback;
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(cleanBase)) {
    cleanBase = '_' + cleanBase;
  }
  const cleanExt = ext.replace(/[. ]+$/g, '').slice(0, 16);
  return cleanBase.slice(0, Math.max(20, 80 - cleanExt.length)) + cleanExt;
}

function htmlText(html) {
  return String(html || '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<(script|style|head)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, '')
    .replace(/<(br|hr)\b[^>]*>/gi, '\n')
    .replace(/<\/(p|div|tr|li|h[1-6]|table|blockquote)>/gi, '\n')
    .replace(/<\/(td|th)>/gi, '\t')
    .replace(/<[^>]*>/g, '')
    .replace(/&(#x[0-9a-f]+|#\d+|nbsp|amp|lt|gt|quot|apos);/gi, (_, v) => {
      if (v[0] === '#') {
        const n = v[1].toLowerCase() === 'x' ? parseInt(v.slice(2), 16) : parseInt(v.slice(1), 10);
        return n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : '';
      }
      return { nbsp: ' ', amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }[v.toLowerCase()];
    })
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function toRelUrl(baseDir, targetPath) {
  return path.relative(baseDir, targetPath).split(path.sep).map(encodeURIComponent).join('/');
}

function collectFolders(rootFolder) {
  const folders = [];
  let totalItems = 0;
  function walk(folder, parentPath) {
    const name = folder.displayName || '';
    const relPath = name ? (parentPath ? `${parentPath}/${name}` : name) : parentPath;
    const count = Number(folder.contentCount || 0);
    if (count > 0 || name) {
      folders.push({ path: relPath || '(루트)', name: name || '(루트)', items: count });
      totalItems += count;
    }
    if (folder.hasSubfolders) {
      let subs = [];
      try {
        subs = folder.getSubFolders();
      } catch (_) {
        subs = [];
      }
      for (const sub of subs) {
        walk(sub, relPath);
      }
    }
  }
  walk(rootFolder, '');
  return { folders, totalItems };
}

function inspectPst(pstPath) {
  const realPath = fs.realpathSync(pstPath);
  const st = fs.statSync(realPath);
  const pst = new PSTFile(realPath);
  try {
    const { folders, totalItems } = collectFolders(pst.getRootFolder());
    const nonZeroFolders = folders.filter(f => f.items > 0);
    return {
      mode: 'inspect',
      source: realPath,
      name: path.basename(realPath),
      bytes: st.size,
      mtime: st.mtime.toISOString(),
      totalItems,
      folderCount: nonZeroFolders.length,
      folders: nonZeroFolders.slice(0, 200),
    };
  } finally {
    pst.close();
  }
}

function convertPst(pstPath, outputDir, sourceLabel, limit = 0) {
  const realPath = fs.realpathSync(pstPath);
  const before = fs.statSync(realPath);
  fs.mkdirSync(outputDir, { recursive: true });

  const pst = new PSTFile(realPath);
  const report = {
    source: realPath,
    sourceName: path.basename(realPath),
    sourceBytes: before.size,
    outputDir,
    expected: 0,
    exported: 0,
    skippedNonMail: 0,
    attachments: 0,
    attachmentBytes: 0,
    years: {},
    warnings: [],
    errors: [],
  };
  const records = [];
  let lastEmitAt = 0;

  function emitProgress(folderName, subject, force = false) {
    const now = Date.now();
    if (!force && report.exported % 20 !== 0 && now - lastEmitAt < 250) return;
    lastEmitAt = now;
    process.stdout.write(
      'PROGRESS:' + JSON.stringify({
        exported: report.exported,
        expected: report.expected,
        attachments: report.attachments,
        warnings: report.warnings.length,
        errors: report.errors.length,
        folder: folderName,
        subject: String(subject || '').slice(0, 80),
        years: report.years,
      }) + '\n'
    );
  }

  try {
    const summary = collectFolders(pst.getRootFolder());
    report.expected = summary.totalItems;
    emitProgress('준비 중', '', true);

    function walkFolder(folder) {
      if (limit > 0 && report.exported >= limit) return;
      const folderCount = Number(folder.contentCount || 0);
      const folderLabel = folder.displayName || '받은메일함';
      const safeFolder = safeName(folderLabel, '메일폴더');

      if (folderCount > 0) {
        while (true) {
          if (limit > 0 && report.exported >= limit) break;
          let m;
          try {
            m = folder.getNextChild();
          } catch (err) {
            report.errors.push({ folder: folderLabel, error: err && err.message ? err.message : String(err) });
            break;
          }
          if (!m) break;

          const id = m.descriptorNodeId ? m.descriptorNodeId.toString() : String(report.exported + 1);
          const msgClass = String(m.messageClass || '');
          if (msgClass && !msgClass.startsWith('IPM.Note')) {
            report.skippedNonMail++;
            continue;
          }

          try {
            const rawDt = m.messageDeliveryTime || m.clientSubmitTime;
            const validDate = rawDt && !isNaN(rawDt) && rawDt.getFullYear() >= 1970 && rawDt.getFullYear() <= 2100;
            const dtIso = validDate ? rawDt.toISOString() : '';
            const year = validDate ? String(rawDt.getFullYear()) : '날짜미확인';

            let dir = path.join(outputDir, year, safeFolder, safeName(id, 'msg'));
            if (fs.existsSync(dir)) {
              let suffix = 2;
              while (fs.existsSync(`${dir}_${suffix}`)) suffix++;
              dir = `${dir}_${suffix}`;
            }
            fs.mkdirSync(dir, { recursive: true });

            const subject = m.subject || '(제목 없음)';
            const sender = m.senderEmailAddress || '';
            const senderName = m.senderName || '';
            const metadata = {
              id,
              folder: folderLabel,
              date: dtIso,
              subject,
              sender,
              senderName,
              to: m.displayTo || '',
              cc: m.displayCC || '',
              messageClass: msgClass || 'IPM.Note',
              headers: m.transportMessageHeaders || '',
              attachments: [],
            };

            const body = m.body || '';
            const html = m.bodyHTML || '';
            const headerText = `제목: ${subject}\n발신: ${senderName} <${sender}>\n날짜: ${dtIso || String(rawDt || '')}\n\n`;
            const bodyFileText = headerText + body;
            const bodyPath = path.join(dir, '본문.txt');
            fs.writeFileSync(bodyPath, bodyFileText, 'utf8');

            const htmlPath = path.join(dir, 'HTML원문.txt');
            if (html) {
              fs.writeFileSync(htmlPath, html, 'utf8');
            }

            const numAtt = Number(m.numberOfAttachments || 0);
            const uiAttachments = [];
            for (let i = 0; i < numAtt; i++) {
              let a;
              try {
                a = m.getAttachment(i);
              } catch (attErr) {
                const msg = attErr && attErr.message ? attErr.message : String(attErr);
                report.warnings.push({ id, attachment: i, error: msg });
                metadata.attachments.push({ index: i, name: `attachment_${i + 1}`, error: msg });
                continue;
              }
              const origName = a.longFilename || a.filename || `attachment_${i + 1}`;
              const entry = { index: i, name: origName, method: a.attachMethod };
              let stream = null;
              try {
                stream = a.fileInputStream;
              } catch (sErr) {
                stream = null;
              }
              if (!stream) {
                entry.error = '첨부 스트림 없음 (임베디드/인라인 항목)';
                report.warnings.push({ id, name: origName, error: entry.error });
                metadata.attachments.push(entry);
                continue;
              }

              const expectedBytes = Number(stream.length ? stream.length.toString() : 0);
              const outFileName = `${i + 1}_${safeFileName(origName, 'attachment')}`;
              const filePath = path.join(dir, outFileName);
              const buf = Buffer.alloc(8176);
              const hash = crypto.createHash('sha256');
              let total = 0;
              let fd = null;
              try {
                fd = fs.openSync(filePath, 'wx');
                while (total < expectedBytes) {
                  const n = stream.read(buf);
                  if (n <= 0) break;
                  fs.writeSync(fd, buf, 0, n);
                  hash.update(buf.subarray(0, n));
                  total += n;
                }
                fs.closeSync(fd);
                fd = null;
                if (expectedBytes > 0 && total !== expectedBytes) {
                  throw new Error(`첨부 길이 불일치 (${total}/${expectedBytes})`);
                }
                entry.expectedBytes = expectedBytes;
                entry.savedBytes = total;
                entry.sha256 = hash.digest('hex');
                entry.savedName = outFileName;
                report.attachments++;
                report.attachmentBytes += total;
                uiAttachments.push({ name: origName, url: toRelUrl(outputDir, filePath) });
              } catch (ioErr) {
                if (fd !== null) {
                  try { fs.closeSync(fd); } catch (_) {}
                }
                try { fs.unlinkSync(filePath); } catch (_) {}
                entry.error = ioErr && ioErr.message ? ioErr.message : String(ioErr);
                report.warnings.push({ id, name: origName, error: entry.error });
              }
              metadata.attachments.push(entry);
            }

            fs.writeFileSync(path.join(dir, '메일정보.json'), JSON.stringify(metadata, null, 2), 'utf8');

            let searchBodyText = bodyFileText;
            if (!body.trim() && html) {
              const fallback = htmlText(html);
              if (fallback) searchBodyText = headerText + fallback;
            }

            const fingerprint = sha256(
              JSON.stringify({
                subject,
                date: dtIso,
                sender,
                senderName,
                to: metadata.to,
                cc: metadata.cc,
                messageClass: metadata.messageClass,
                headers: metadata.headers,
                body: sha256(fs.readFileSync(bodyPath)),
                html: html ? sha256(fs.readFileSync(htmlPath)) : null,
                attachments: metadata.attachments
                  .filter(a => a.savedName && a.sha256)
                  .map(a => ({ name: a.name, method: a.method, size: a.savedBytes, hash: a.sha256 })),
              })
            );

            records.push({
              fingerprint,
              id,
              date: dtIso,
              year,
              subject,
              sender: [senderName, sender].filter(Boolean).join(' · '),
              body: toRelUrl(outputDir, bodyPath),
              bodyText: searchBodyText,
              source: sourceLabel || path.basename(realPath),
              attachments: uiAttachments,
            });

            report.exported++;
            report.years[year] = (report.years[year] || 0) + 1;
            emitProgress(folderLabel, subject, false);
          } catch (mailErr) {
            report.errors.push({ id, folder: folderLabel, error: mailErr && mailErr.message ? mailErr.message : String(mailErr) });
          }
        }
      }

      if (folder.hasSubfolders) {
        let subs = [];
        try {
          subs = folder.getSubFolders();
        } catch (_) {
          subs = [];
        }
        for (const sub of subs) {
          if (limit > 0 && report.exported >= limit) break;
          walkFolder(sub);
        }
      }
    }

    walkFolder(pst.getRootFolder());
    emitProgress('저장 마무리 중', '', true);
  } finally {
    pst.close();
  }

  records.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  const recordsFile = path.join(outputDir, '검색레코드.json');
  fs.writeFileSync(recordsFile, JSON.stringify(records), 'utf8');
  fs.writeFileSync(path.join(outputDir, '추출결과.json'), JSON.stringify(report, null, 2), 'utf8');
  fs.writeFileSync(
    path.join(outputDir, '추가추출정보.json'),
    JSON.stringify({
      source: realPath,
      sourceBytes: before.size,
      sourceMtime: before.mtime.toISOString(),
      created: new Date().toISOString(),
      output: outputDir,
      mailOnly: true,
    }, null, 2),
    'utf8'
  );
  fs.writeFileSync(
    path.join(outputDir, '읽어주세요.txt'),
    '이 폴더는 Outlook PST 파일에서 연도별·폴더별로 추출한 메일 보관함입니다.\n각 메일 폴더의 본문.txt, HTML원문.txt, 메일정보.json 및 첨부파일 원본을 확인할 수 있습니다.\n',
    'utf8'
  );

  process.stdout.write(
    'RESULT:' + JSON.stringify({
      ok: true,
      exported: report.exported,
      expected: report.expected,
      skippedNonMail: report.skippedNonMail,
      attachments: report.attachments,
      attachmentBytes: report.attachmentBytes,
      warnings: report.warnings.length,
      errors: report.errors.length,
      years: report.years,
      recordsFile,
      outputDir,
    }) + '\n'
  );
}

function main() {
  const args = process.argv.slice(2);
  const mode = args[0];
  if (mode === '--inspect') {
    const pstPath = args[1];
    if (!pstPath) throw new Error('PST 경로가 필요합니다.');
    process.stdout.write(JSON.stringify(inspectPst(pstPath)) + '\n');
    return;
  }
  if (mode === '--convert') {
    const posArgs = [];
    let limit = 0;
    for (let i = 1; i < args.length; i++) {
      if (args[i] === '--limit' && i + 1 < args.length) {
        limit = Math.max(0, parseInt(args[++i], 10) || 0);
      } else {
        posArgs.push(args[i]);
      }
    }
    const [pstPath, outputDir, sourceLabel] = posArgs;
    if (!pstPath || !outputDir) throw new Error('PST 경로와 출력 경로가 필요합니다.');
    convertPst(pstPath, outputDir, sourceLabel || '', limit);
    return;
  }
  throw new Error('사용법: pst_worker.cjs --inspect <pst> | --convert <pst> <outDir> [label]');
}

try {
  main();
} catch (err) {
  process.stderr.write((err && err.stack ? err.stack : String(err)) + '\n');
  process.exit(1);
}
