// ═══════════════════════════════════════════════════
// Tennis Weather Dashboard — Frontend Controller
// ═══════════════════════════════════════════════════

const FORECAST_URL = '../output/forecast.json';
const DIAGNOSIS_URL = '../output/diagnosis.json';
let _forecastData = null; // stored globally for cross-card metric access

// ─── NTP Clock (synced via timeapi.io) ───
let _ntpOffset = 0; // ms offset: server_time - local_time
(async function syncNTP() {
    try {
        const before = Date.now();
        const res = await fetch('https://timeapi.io/api/time/current/zone?timeZone=Asia/Macau');
        const rtt = Date.now() - before;
        const data = await res.json();
        const serverMs = new Date(data.dateTime).getTime();
        _ntpOffset = serverMs - (before + rtt / 2);
    } catch (e) {
        console.warn('NTP sync failed, using local time');
    }
    tickNTP();
    setInterval(tickNTP, 1000);
})();

function tickNTP() {
    const now = new Date(Date.now() + _ntpOffset);
    const h = String(now.getHours()).padStart(2, '0');
    const m = String(now.getMinutes()).padStart(2, '0');
    const s = String(now.getSeconds()).padStart(2, '0');
    const eH = document.getElementById('ntp-h');
    const eM = document.getElementById('ntp-m');
    const eS = document.getElementById('ntp-s');
    if (eH) eH.textContent = h;
    if (eM) eM.textContent = m;
    if (eS) eS.textContent = s;
}

async function fetchDashboardData() {
    const t = Date.now();
    try {
        const [fRes, dRes] = await Promise.all([
            fetch(`${FORECAST_URL}?t=${t}`),
            fetch(`${DIAGNOSIS_URL}?t=${t}`)
        ]);
        if (fRes.ok) { _forecastData = await fRes.json(); updateForecastUI(_forecastData); }
        if (dRes.ok) updateDiagnosisUI(await dRes.json());
    } catch (e) {
        console.error('Fetch error:', e);
    }
}

// ─── Forecast ───
function updateForecastUI(data) {
    setText('court-name', data.court?.name || '未知位置');
    if (data.court?.lon && data.court?.lat) {
        setText('court-coords', `${data.court.lat}°N  ${data.court.lon}°E`);
    }
    if (data.generated_at) {
        const d = new Date(data.generated_at);
        setText('update-time', `最后更新: ${d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`);
    }

    // Booking decision
    if (data.booking) {
        const b = data.booking;
        setText('booking-decision', b.decision_cn || b.decision);
        const wEl = document.getElementById('booking-window');
        if (wEl) { wEl.innerHTML = ''; wEl.append(lucideI('clock', 15, 'var(--color-text-muted)'), ` ${b.play_window} (${b.lead_time_hours}小时后)`); }

        if (b.check_again_at) {
            const rc = document.getElementById('hero-recheck');
            if (rc) { rc.style.display = 'inline-flex'; setText('recheck-time', `${b.check_again_at} 复查`); }
        }

        const pos = b.decision === 'keep_booking';
        const neu = b.decision === 'keep_but_recheck';
        const bar = document.getElementById('hero-bar');
        const ico = document.getElementById('status-icon');
        if (pos) setHero(bar, ico, 'good', 'green', 'check-circle', 'var(--green)');
        else if (neu) setHero(bar, ico, 'warn', 'amber', 'alert-triangle', 'var(--amber)');
        else setHero(bar, ico, 'bad', 'red', 'x-circle', 'var(--red)');

        const ul = document.getElementById('booking-reasons');
        if (ul) {
            ul.innerHTML = '';
            (b.reason || []).forEach(r => ul.appendChild(mkReason(r, 'info', false)));
            if (b.caveat && b.caveat[0] !== '无特别注意事项')
                b.caveat.forEach(c => ul.appendChild(mkReason(c, 'alert-circle', true)));
        }
    }

    // Realtime conditions
    if (data.station_realtime) {
        const rt = data.station_realtime;
        setText('rt-temp', `${rt.temperature ?? '--'}°C`);
        setText('rt-humidity', `${rt.humidity_pct ?? '--'}%`);
        setText('rt-wind', `${rt.wind_direction || ''} ${rt.wind_power_level || ''}`);
        setText('rt-state', `${rt.weather_state || '--'} / AQI ${rt.aqi ?? '--'}`);
        if (rt.hourly_forecast?.length) renderHourly(rt.hourly_forecast);
        if (rt.seven_day_forecast?.length) {
            renderWeek(rt.seven_day_forecast);
            renderHeroWx(rt.seven_day_forecast);
        }
        // Rain message banner
        const msgEl = document.getElementById('rain-msg');
        const msgText = document.getElementById('rain-msg-text');
        if (msgEl && msgText && rt.rain_2h_message) {
            msgText.textContent = rt.rain_2h_message;
            if (rt.rain_2h_flag === 1 || rt.rain_2h_flag === '1') msgEl.classList.add('has-rain');
            else msgEl.classList.remove('has-rain');
        }
    }

    // Hero KPI metrics
    if (data.max_dbz_nearby) {
        const dbz30 = data.max_dbz_nearby['30min'] ?? '--';
        const dbzEl = document.getElementById('kpi-dbz');
        if (dbzEl) {
            dbzEl.textContent = dbz30;
            dbzEl.style.color = dbz30 >= 35 ? 'var(--red)' : dbz30 >= 20 ? 'var(--amber)' : 'var(--green)';
        }
    }
    if (data.playable_coverage_ratio) {
        const cov = data.playable_coverage_ratio['30min'];
        const covEl = document.getElementById('kpi-coverage');
        if (covEl && cov != null) {
            const pct = ((1 - cov) * 100).toFixed(0);
            covEl.textContent = `${pct}%`;
            covEl.style.color = pct >= 90 ? 'var(--green)' : pct >= 70 ? 'var(--amber)' : 'var(--red)';
        }
    }
    if (data.booking) {
        const rh = data.booking.window_hourly_rain_count ?? '--';
        const rhEl = document.getElementById('kpi-rain-hrs');
        if (rhEl) {
            rhEl.textContent = `${rh}h`;
            rhEl.style.color = rh === 0 ? 'var(--green)' : rh <= 1 ? 'var(--amber)' : 'var(--red)';
        }
    }

    // QPF rain chart
    if (data.official_qpf6min?.length) renderQPFChart(data.official_qpf6min);

    // Risk timeline
    if (data.rain_probability) {
        [30, 60, 120].forEach(m => {
            const p = data.rain_probability[`${m}min`] || 0;
            const c = data.confidence?.[`${m}min`] || 'low';
            updRisk(m, p, c);
        });
    }
    if (data.motion) setText('motion-consistency', data.motion.consistency.toFixed(3));

    // Radar Timeline Player
    if (data.radar_frames?.length) {
        initRadarPlayer(data.radar_frames);
    } else {
        // Fallback: static debug image
        const img = document.getElementById('radar-image');
        if (img) {
            const src = data.mapping_debug?.debug_image
                ? `../${data.mapping_debug.debug_image}`
                : '../output/debug_court_radius.png';
            img.src = `${src}?t=${Date.now()}`;
        }
    }

    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// ─── Radar Timeline Player ───
let _radarTimer = null;
let _radarPlaying = true;
let _radarIdx = 0;
let _radarFrames = [];

function initRadarPlayer(frames) {
    // Take last 6 frames
    _radarFrames = frames.slice(-6);
    _radarIdx = _radarFrames.length - 1; // start on latest

    const img = document.getElementById('radar-image');
    const timeline = document.getElementById('radar-timeline');
    const playBtn = document.getElementById('radar-play-btn');
    const timeLabel = document.getElementById('radar-current-time');
    if (!img || !timeline) return;

    // Preload all images
    const t = Date.now();
    const imgCache = _radarFrames.map(f => {
        const i = new window.Image();
        i.src = `../${f.path}?t=${t}`;
        return i;
    });

    // Build timeline dots
    timeline.innerHTML = '';
    _radarFrames.forEach((f, idx) => {
        const dot = document.createElement('button');
        dot.className = `radar-dot${idx === _radarIdx ? ' active' : ''}`;
        dot.innerHTML = `<span class="radar-dot-pip"></span><span class="radar-dot-label">${f.time}</span>`;
        dot.onclick = () => { _radarIdx = idx; showFrame(idx); };
        timeline.appendChild(dot);
    });

    function showFrame(idx) {
        img.style.opacity = '0.3';
        setTimeout(() => {
            img.src = imgCache[idx].src;
            img.style.opacity = '1';
        }, 80);
        timeLabel.textContent = _radarFrames[idx].time;
        timeline.querySelectorAll('.radar-dot').forEach((d, i) => {
            d.classList.toggle('active', i === idx);
        });
    }

    // Play/pause
    function startLoop() {
        if (_radarTimer) clearInterval(_radarTimer);
        _radarTimer = setInterval(() => {
            _radarIdx = (_radarIdx + 1) % _radarFrames.length;
            showFrame(_radarIdx);
        }, 1500);
        _radarPlaying = true;
        if (playBtn) playBtn.innerHTML = '<i data-lucide="pause"></i>';
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    function stopLoop() {
        if (_radarTimer) { clearInterval(_radarTimer); _radarTimer = null; }
        _radarPlaying = false;
        if (playBtn) playBtn.innerHTML = '<i data-lucide="play"></i>';
        if (typeof lucide !== 'undefined') lucide.createIcons();
    }

    if (playBtn) {
        playBtn.onclick = () => { _radarPlaying ? stopLoop() : startLoop(); };
    }

    // Show latest frame first, then start auto-play
    showFrame(_radarIdx);
    // Respect prefers-reduced-motion
    const prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!prefersReduced) {
        setTimeout(() => { _radarIdx = 0; showFrame(0); startLoop(); }, 800);
    }
}

// ─── Diagnosis: 3 separate cards ───
function updateDiagnosisUI(data) {
    // Card 4: AI Summary
    if (data.conclusion) {
        const c = data.conclusion;
        setText('llm-headline', c.headline);
        // Playability status badge
        const statusEl = document.getElementById('ai-playability');
        if (statusEl && c.playability) {
            const statusMap = {
                '可打':      { icon: 'circle-check', cls: 'status-good', desc: '天气条件适合打球' },
                '谨慎可打':  { icon: 'alert-triangle', cls: 'status-warn', desc: '可以打，但需留意天气变化' },
                '不建议开打': { icon: 'x-circle', cls: 'status-bad', desc: '天气条件不佳，建议改期' },
                '立即避雨':  { icon: 'cloud-lightning', cls: 'status-danger', desc: '正在下雨或即将下雨' },
            };
            const s = statusMap[c.playability] || { icon: 'help-circle', cls: 'status-warn', desc: c.playability };
            statusEl.className = `ai-status-badge ${s.cls}`;
            statusEl.innerHTML = `
                <span class="ai-status-icon"><i data-lucide="${s.icon}"></i></span>
                <span class="ai-status-text">${c.playability}</span>
                <span class="ai-status-desc">${s.desc}</span>`;
            const card = document.getElementById('ai-playability-card');
            if (card) card.className = `ai-kpi status-kpi ${s.cls}`;
        }
        setText('ai-court-impact', c.court_impact || '--');
        setText('ai-suggestion', c.suggestion || '--');

        const cb = document.getElementById('ai-confidence');
        if (cb && c.confidence) {
            cb.textContent = { high: '高', medium: '中', low: '低' }[c.confidence] || c.confidence;
            cb.className = `conf ${c.confidence}`;
        }
    }

    // Card 5: Risk Assessment
    if (data.risk_assessment) {
        const ra = data.risk_assessment;
        const grid = document.getElementById('ra-grid');
        if (grid) {
            grid.innerHTML = '';
            const riskMeta = {
                low: { label: '低', tone: '低风险', icon: 'shield-check', score: 22 },
                medium: { label: '中', tone: '需关注', icon: 'shield-alert', score: 56 },
                high: { label: '高', tone: '建议回避', icon: 'octagon-alert', score: 86 },
            };
            const normalizeRisk = (level = '') => {
                if (String(level).includes('高')) return 'high';
                if (String(level).includes('中')) return 'medium';
                return 'low';
            };
            const escapeHtml = (s = '') => String(s).replace(/[&<>"']/g, ch => ({
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
            }[ch]));
            const primaryRisks = [
                { name: '当前', window: 'Now', ...ra.now_rain_risk },
                { name: '30分钟', window: '30m', ...ra.risk_30min },
                { name: '60分钟', window: '60m', ...ra.risk_60min },
                { name: '120分钟', window: '120m', ...ra.risk_120min },
            ];
            // Additional risks — handle both dict {level, reason} and string "低，reason..." formats
            const parseExtraRisk = (v) => {
                if (!v) return null;
                if (typeof v === 'object' && v.level) return v;
                if (typeof v === 'string') {
                    const m = v.match(/^([低中高]+(?:-[低中高]+)?)[，。,.]\s*(.*)$/s);
                    return m ? { level: m[1], reason: m[2] } : { level: '中', reason: v };
                }
                return null;
            };
            const extras = [
                { key: 'approach_risk', name: '回波接近风险' },
                { key: 'landing_probability', name: '落地概率' },
                { key: 'spawning_risk', name: '局地新生风险' },
            ].map(e => {
                const parsed = parseExtraRisk(ra[e.key]);
                return parsed ? { name: e.name, ...parsed } : null;
            }).filter(Boolean);

            const riskIcons = {
                '当前': 'cloud-drizzle',
                '30分钟': 'clock-3',
                '60分钟': 'clock-6',
                '120分钟': 'clock-12',
                '回波接近风险': 'move-right',
                '落地概率': 'droplet',
                '局地新生风险': 'zap',
            };

            const validPrimary = primaryRisks.filter(r => r.level);
            const peak = [...validPrimary, ...extras].reduce((max, r) => {
                const cls = normalizeRisk(r.level);
                return riskMeta[cls].score > riskMeta[normalizeRisk(max.level)].score ? r : max;
            }, validPrimary[0] || extras[0] || { level: '低', name: '当前' });
            const peakClass = normalizeRisk(peak.level);
            const peakMeta = riskMeta[peakClass];
            const peakReason = peak.reason || '暂无详细说明';

            const primaryHtml = validPrimary.map(r => {
                const cls = normalizeRisk(r.level);
                const meta = riskMeta[cls];
                const icon = riskIcons[r.name] || 'circle-dot';
                return `
                    <article class="ra-risk-card ${cls}" aria-label="${escapeHtml(r.name)}${escapeHtml(r.level)}">
                        <div class="ra-risk-top">
                            <span class="ra-window">${escapeHtml(r.window)}</span>
                            <span class="ra-level ${cls}">${escapeHtml(r.level)}</span>
                        </div>
                        <div class="ra-risk-main">
                            <span class="ra-risk-icon"><i data-lucide="${icon}"></i></span>
                            <span class="ra-risk-name">${escapeHtml(r.name)}</span>
                        </div>
                        <div class="ra-meter" aria-hidden="true"><span class="${cls}" style="width:${meta.score}%"></span></div>
                        <p class="ra-risk-reason">${escapeHtml(r.reason || '')}</p>
                    </article>`;
            }).join('');

            const extraHtml = extras.map(r => {
                if (!r.level) return;
                const cls = normalizeRisk(r.level);
                const icon = riskIcons[r.name] || 'circle-dot';
                return `
                    <div class="ra-extra ${cls}">
                        <span class="ra-extra-icon"><i data-lucide="${icon}"></i></span>
                        <span class="ra-extra-copy">
                            <span class="ra-extra-title">${escapeHtml(r.name)}</span>
                            <span class="ra-extra-reason">${escapeHtml(r.reason || '')}</span>
                        </span>
                        <span class="ra-level ${cls}">${escapeHtml(r.level)}</span>
                    </div>`;
            }).join('');

            grid.innerHTML = `
                <section class="ra-overview ${peakClass}">
                    <div class="ra-overview-icon"><i data-lucide="${peakMeta.icon}"></i></div>
                    <div class="ra-overview-copy">
                        <span class="ra-overview-label">最高关注点 · ${escapeHtml(peak.name)}</span>
                        <strong>${peakMeta.tone}</strong>
                        <p>${escapeHtml(peakReason)}</p>
                    </div>
                </section>
                <section class="ra-primary" aria-label="时间窗口风险">
                    ${primaryHtml}
                </section>
                ${extraHtml ? `<section class="ra-extras" aria-label="辅助风险">${extraHtml}</section>` : ''}`;
        }
    }

    // Data Sources — render inside AI Summary card with key metrics from forecast.json
    if (data.data_summary) {
        const ds = data.data_summary;
        const section = document.getElementById('ds-section');
        const grid = document.getElementById('ds-grid');
        if (grid && section) {
            grid.innerHTML = '';

            // Color classifier for metric chips: good(green) / warn(amber) / bad(red)
            const chipColor = (key, rawVal) => {
                const n = parseFloat(rawVal);
                const rules = {
                    '最大回波': v => v < 15 ? 'good' : v < 35 ? 'warn' : 'bad',
                    '回波覆盖': v => v < 5 ? 'good' : v < 20 ? 'warn' : 'bad',
                    '可雨覆盖': v => v < 1 ? 'good' : v < 5 ? 'warn' : 'bad',
                    '上游最大': v => v < 20 ? 'good' : v < 35 ? 'warn' : 'bad',
                    '最大降雨量': v => v === 0 ? 'good' : v < 2 ? 'warn' : 'bad',
                    '气温': v => v < 30 ? 'good' : v < 35 ? 'warn' : 'bad',
                    '湿度': v => v < 70 ? 'good' : v < 85 ? 'warn' : 'bad',
                    '风速': v => v < 3 ? 'good' : v < 6 ? 'warn' : 'bad',
                    'AQI': v => v <= 50 ? 'good' : v <= 100 ? 'warn' : 'bad',
                };
                if (rules[key] && !isNaN(n)) return rules[key](n);
                // String-based rules
                if (key === '上游等级') return { none: 'good', trace: 'good', weak: 'warn', organized: 'bad', strong: 'bad' }[rawVal] || 'warn';
                if (key === '短临标志') return rawVal === '无雨' ? 'good' : 'bad';
                if (key === '覆盖时长') return 'good';
                if (key === '今日白天' || key === '今夜') return /雨|雷/.test(rawVal) ? 'bad' : /阴|云/.test(rawVal) ? 'warn' : 'good';
                return '';
            };

            // Build metric chips from forecast data
            const f = _forecastData || {};
            const st = f.station_realtime || {};
            const cur = f.current || {};
            const radarMetrics = [
                cur.max_dbz_nearby != null ? { k: '最大回波', v: `${cur.max_dbz_nearby} dBZ` } : null,
                cur.echo_coverage != null ? { k: '回波覆盖', v: `${(cur.echo_coverage * 100).toFixed(1)}%` } : null,
                cur.playable_coverage != null ? { k: '可雨覆盖', v: `${(cur.playable_coverage * 100).toFixed(1)}%` } : null,
                f.upstream_echo?.upstream_max_dbz != null ? { k: '上游最大', v: `${f.upstream_echo.upstream_max_dbz} dBZ` } : null,
                f.upstream_echo?.upstream_level ? { k: '上游等级', v: f.upstream_echo.upstream_level } : null,
            ].filter(Boolean);

            const qpfMetrics = [
                f.official_qpf6min_summary?.total_minutes != null ? { k: '覆盖时长', v: `${f.official_qpf6min_summary.total_minutes}分钟` } : null,
                f.official_qpf6min_summary?.max_value != null ? { k: '最大降雨量', v: `${f.official_qpf6min_summary.max_value}` } : null,
                st.rain_2h_flag != null ? { k: '短临标志', v: st.rain_2h_flag ? '有雨' : '无雨' } : null,
            ].filter(Boolean);

            const rtMetrics = [
                st.temperature != null ? { k: '气温', v: `${st.temperature}°C` } : null,
                st.humidity_pct != null ? { k: '湿度', v: `${st.humidity_pct}%` } : null,
                st.wind_speed_mps != null ? { k: '风速', v: `${st.wind_speed_mps} m/s` } : null,
                st.aqi != null ? { k: 'AQI', v: `${st.aqi}` } : null,
            ].filter(Boolean);

            const bgMetrics = [
                st.seven_day_forecast?.[0]?.weather_day ? { k: '今日白天', v: st.seven_day_forecast[0].weather_day } : null,
                st.seven_day_forecast?.[0]?.weather_night ? { k: '今夜', v: st.seven_day_forecast[0].weather_night } : null,
            ].filter(Boolean);

            const metricsMap = { '雷达分析': radarMetrics, '官方短临': qpfMetrics, '实况观测': rtMetrics, '天气背景': bgMetrics };

            // Chip key → Lucide icon mapping
            const chipIcon = {
                '最大回波': 'signal', '回波覆盖': 'scan', '可雨覆盖': 'cloud-rain',
                '上游最大': 'arrow-up-right', '上游等级': 'layers',
                '覆盖时长': 'timer', '最大降雨量': 'droplets', '短临标志': 'flag',
                '气温': 'thermometer', '湿度': 'droplet', '风速': 'wind', 'AQI': 'leaf',
                '今日白天': 'sun', '今夜': 'moon',
            };

            const panels = [
                { icon: 'radar', label: '雷达分析', val: ds.radar },
                { icon: 'cloud-rain', label: '官方短临', val: ds.official_forecast },
                { icon: 'thermometer', label: '实况观测', val: ds.realtime },
                { icon: 'calendar', label: '天气背景', val: ds.background },
            ];
            let hasContent = false;
            panels.forEach(p => {
                if (!p.val) return;
                hasContent = true;
                const metrics = metricsMap[p.label] || [];
                const chipsHtml = metrics.length
                    ? `<div class="ds-chips">${metrics.map(m => { const ico = chipIcon[m.k] || ''; return `<span class="ds-chip">${ico ? `<i data-lucide="${ico}" class="ds-chip-ico"></i>` : ''}<span class="ds-chip-k">${m.k}</span><span class="ds-chip-v ${chipColor(m.k, m.v)}">${m.v}</span></span>`; }).join('')}</div>`
                    : '';
                const el = document.createElement('div');
                el.className = 'ds-item';
                el.innerHTML = `
                    <div class="ds-label"><i data-lucide="${p.icon}"></i>${p.label}</div>
                    <div class="ds-text">${p.val}</div>
                    ${chipsHtml}`;
                grid.appendChild(el);
            });
            if (hasContent) section.style.display = 'block';
        }
    }

    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// ─── Weather emoji mapper ───
function weatherIcon(wx) {
    const map = [
        [/暴雨|大暴|雷/, 'cloud-lightning', 'wx-storm'],
        [/大雨|中雨/, 'cloud-rain', 'wx-rain'],
        [/小雨|阵雨/, 'cloud-drizzle', 'wx-drizzle'],
        [/雨/, 'cloud-rain-wind', 'wx-rain'],
        [/雪/, 'snowflake', 'wx-snow'],
        [/雾|霾/, 'cloud-fog', 'wx-fog'],
        [/阴/, 'cloud', 'wx-overcast'],
        [/多云/, 'cloud-sun', 'wx-partly'],
        [/晴/, 'sun', 'wx-sunny'],
    ];
    let icon = 'cloud-sun', cls = 'wx-partly';
    for (const [re, ic, c] of map) { if (wx && re.test(wx)) { icon = ic; cls = c; break; } }
    return `<i data-lucide="${icon}" class="wx-icon ${cls}"></i>`;
}

// ─── Hourly ───
function renderHourly(hours) {
    const c = document.getElementById('hourly-forecast-container');
    if (!c) return;
    c.innerHTML = '';
    hours.forEach(h => {
        const rain = h.weather.includes('雨');
        const d = document.createElement('div');
        d.className = `h-card${rain ? ' rain' : ''}`;
        d.innerHTML = `
            <span class="h-time">${h.time}</span>
            <span class="h-icon">${weatherIcon(h.weather)}</span>
            <span class="h-temp" ${h.temp > 30 ? 'style="color:var(--amber)"' : ''}>${h.temp}°</span>
            <span class="h-wx${rain ? ' rain-t' : ''}">${h.weather}</span>
            <span class="h-wind">${h.wind_dir || ''} ${h.wind_power || ''}</span>`;
        c.appendChild(d);
    });
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// ─── 7-Day ───
function renderWeek(days) {
    const c = document.getElementById('seven-day-container');
    if (!c) return;
    c.innerHTML = '';
    days.forEach(d => {
        const today = d.label === '今天';
        const hasRain = (d.weather_day + d.weather_night).includes('雨');
        const el = document.createElement('div');
        el.className = `d-card${today ? ' today' : ''}`;
        el.innerHTML = `
            <span class="d-date">${d.date}</span>
            <span class="d-label">${d.label}</span>
            <span class="d-icon">${weatherIcon(d.weather_day)}</span>
            <span class="d-wx" ${hasRain ? 'style="color:var(--blue)"' : ''}>${d.weather_day}</span>
            <span class="d-temps"><span class="t-hi">${d.temp_max}°</span><span style="color:var(--color-text-muted)"> / </span><span class="t-lo">${d.temp_min}°</span></span>`;
        c.appendChild(el);
    });
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// ─── Risk Row ───
function updRisk(mins, prob, confidence) {
    const pct = (prob * 100).toFixed(0);
    setText(`risk-${mins}-prob`, `${pct}%`);
    let lvl, cls;
    if (prob > 0.5) { lvl = '高'; cls = 'high'; }
    else if (prob > 0.2) { lvl = '中'; cls = 'med'; }
    else { lvl = '低'; cls = 'low'; }

    const le = document.getElementById(`risk-${mins}-level`);
    if (le) { le.textContent = lvl; le.className = `badge ${cls}`; }
    const bar = document.getElementById(`risk-${mins}-bar`);
    if (bar) {
        bar.style.width = `${Math.max(prob * 100, 3)}%`;
        bar.className = `risk-fill ${cls}`;
        // Keep the card accent synced with the computed risk level.
        const row = bar.closest('.risk-row');
        if (row) {
            const colors = { low: 'var(--green)', med: 'var(--amber)', high: 'var(--red)' };
            row.className = `risk-row ${cls}`;
            row.style.borderTopColor = colors[cls] || 'var(--green)';
        }
    }
    const ce = document.getElementById(`conf-${mins}`);
    if (ce) {
        ce.textContent = { high: '高', medium: '中', low: '低' }[confidence] || confidence;
        ce.className = `conf ${confidence}`;
    }
}

// ─── Helpers ───
function setText(id, t) { const e = document.getElementById(id); if (e) e.textContent = t; }
function lucideI(name, sz, color) { const i = document.createElement('i'); i.setAttribute('data-lucide', name); i.style.cssText = `width:${sz}px;height:${sz}px;${color ? 'color:' + color : ''}`; return i; }
function mkReason(text, icon, caveat) {
    const li = document.createElement('li');
    li.className = `reason${caveat ? ' caveat' : ''}`;
    li.append(lucideI(icon, 14), (() => { const s = document.createElement('span'); s.textContent = text; return s; })());
    return li;
}
function setHero(bar, ico, barCls, glowCls, iconName, iconColor) {
    if (bar) bar.className = `hero-bar ${barCls}`;
    if (ico) { ico.className = `status-icon gl-${glowCls}`; ico.innerHTML = ''; ico.append(lucideI(iconName, 26, iconColor)); }
}

// ─── Hero Today/Tomorrow Weather ───
function renderHeroWx(days) {
    const today = days.find(d => d.label === '今天');
    const tmr = days.find(d => d.label === '明天');
    const tmrIdx = days.findIndex(d => d.label === '明天');
    const afterTmr = days.find(d => d.label === '后天') || (tmrIdx >= 0 ? days[tmrIdx + 1] : null);
    const fillHeroDay = (day, ids) => {
        if (!day) return;
        const ic = document.getElementById(ids.icon);
        if (ic) ic.innerHTML = weatherIcon(day.weather_day);
        setText(ids.hi, day.temp_max);
        setText(ids.lo, day.temp_min);
        const desc = document.getElementById(ids.wx);
        if (desc) {
            desc.textContent = day.weather_day;
            desc.style.color = day.weather_day?.includes('雨') ? 'var(--blue)' : '';
        }
    };
    if (today) {
        fillHeroDay(today, { icon: 'hero-today-icon', hi: 'hero-today-hi', lo: 'hero-today-lo', wx: 'hero-today-wx' });
    }
    if (tmr) {
        fillHeroDay(tmr, { icon: 'hero-tmr-icon', hi: 'hero-tmr-hi', lo: 'hero-tmr-lo', wx: 'hero-tmr-wx' });
    }
    if (afterTmr && afterTmr !== today && afterTmr !== tmr) {
        fillHeroDay(afterTmr, { icon: 'hero-after-icon', hi: 'hero-after-hi', lo: 'hero-after-lo', wx: 'hero-after-wx' });
    }
    if (typeof lucide !== 'undefined') lucide.createIcons();
}

// ─── QPF Rain Intensity Chart (SVG area) ───
function renderQPFChart(qpf) {
    const svg = document.getElementById('qpf-chart');
    const timesEl = document.getElementById('qpf-times');
    if (!svg || !qpf.length) return;

    const W = 400, H = 80;
    const maxR = 10; // 10mm = heavy rain cap
    const vals = qpf.map(p => Math.min(parseFloat(p.r) || 0, maxR));
    const peak = Math.max(...vals);
    const n = vals.length;
    const wrap = svg.closest('.hero-rain-chart');
    if (wrap) {
        const qpfClass = peak <= 0 ? 'qpf-flat' : peak < 1 ? 'qpf-light' : 'qpf-active';
        wrap.className = `hero-rain-chart ${qpfClass}`;
    }
    svg.setAttribute('aria-label', peak <= 0 ? '未来两小时无降水趋势' : '未来两小时降水趋势');

    // Build path
    const dx = W / (n - 1 || 1);
    let pathD = `M0,${H}`;
    vals.forEach((v, i) => {
        const x = i * dx;
        const y = H - (v / maxR) * (H - 4);
        pathD += ` L${x},${y}`;
    });
    pathD += ` L${W},${H} Z`;

    const gridLines = peak > 0 ? `
        <line x1="0" y1="${H - (2.5/maxR)*(H-4)}" x2="${W}" y2="${H - (2.5/maxR)*(H-4)}" stroke="rgba(100,116,139,0.1)" stroke-dasharray="2 3"/>
        <line x1="0" y1="${H - (5/maxR)*(H-4)}" x2="${W}" y2="${H - (5/maxR)*(H-4)}" stroke="rgba(100,116,139,0.08)" stroke-dasharray="2 3"/>
        <line x1="0" y1="${H - (7.5/maxR)*(H-4)}" x2="${W}" y2="${H - (7.5/maxR)*(H-4)}" stroke="rgba(100,116,139,0.1)" stroke-dasharray="2 3"/>`
        : `<line x1="0" y1="${H - 8}" x2="${W}" y2="${H - 8}" stroke="rgba(59,130,246,0.45)" stroke-width="1"/>`;

    // Gradient fill — clean chart, no text
    svg.innerHTML = `
        <defs>
            <linearGradient id="rain-grad" x1="0" y1="1" x2="0" y2="0">
                <stop offset="0%" stop-color="rgba(34,197,94,0.1)"/>
                <stop offset="30%" stop-color="rgba(34,197,94,0.25)"/>
                <stop offset="60%" stop-color="rgba(245,158,11,0.35)"/>
                <stop offset="100%" stop-color="rgba(239,68,68,0.45)"/>
            </linearGradient>
        </defs>
        ${gridLines}
        ${peak > 0 ? `<path d="${pathD}" fill="url(#rain-grad)" stroke="none"/>` : ''}
        <polyline points="${vals.map((v,i) => `${i*dx},${H-(v/maxR)*(H-4)}`).join(' ')}"
            fill="none" stroke="rgba(59,130,246,0.5)" stroke-width="1.2" stroke-linejoin="round"/>
    `;

    // Time labels
    if (timesEl) {
        const step = Math.max(1, Math.floor(n / 6));
        timesEl.innerHTML = '';
        for (let i = 0; i < n; i += step) {
            const t = qpf[i].dt.split(' ')[1]?.slice(0, 5) || '';
            timesEl.innerHTML += `<span>${t}</span>`;
        }
    }
}

// ─── Init ───
fetchDashboardData();
setInterval(fetchDashboardData, 30000);
