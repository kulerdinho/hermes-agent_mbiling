'use strict'
/**
 * Navigateur des Mains Mbiling — côté PC (lot 6).
 *
 * Lance le navigateur DÉJÀ présent sur le PC (Edge ou Chrome) avec un profil réservé à Mbiling,
 * fenêtre visible, port de débogage ouvert uniquement sur 127.0.0.1, puis relaie le protocole
 * CDP entre le Relais et ce navigateur. Les sites interdits par la politique locale sont refusés
 * ici, sur le PC, avant d'atteindre le navigateur.
 *
 * Aucune valeur n'est écrite ici : tout vient de la configuration des Mains (clé « navigateur »)
 * et de la politique (clé « sites_interdits »).
 *
 * Messages échangés avec le Relais (sur la connexion des Mains) :
 *   Relais -> PC : {t:"cdp_http", id, methode, chemin}         PC -> Relais : {t:"cdp_http_rep", id, statut, corps}
 *   Relais -> PC : {t:"cdp_ouvrir", canal, chemin}              PC -> Relais : {t:"cdp_ouvert", canal} | {t:"cdp_erreur", canal, raison}
 *   dans les deux sens : {t:"cdp", canal, d}   puis {t:"cdp_fermer", canal}
 */
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

/** Méthodes CDP qui ouvrent une adresse : contrôlées par la politique locale. */
const METHODES_NAVIGATION = new Set(['Page.navigate', 'Target.createTarget'])

function candidatsNavigateur(prefere) {
  const pf = process.env.ProgramFiles || 'C:\\Program Files'
  const pf86 = process.env['ProgramFiles(x86)'] || 'C:\\Program Files (x86)'
  const local = process.env.LOCALAPPDATA || ''
  const edge = [
    path.join(pf86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    path.join(pf, 'Microsoft', 'Edge', 'Application', 'msedge.exe')
  ]
  const chrome = [
    path.join(pf, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    path.join(pf86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
    path.join(local, 'Google', 'Chrome', 'Application', 'chrome.exe')
  ]
  const autres = ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
  return (prefere === 'chrome' ? [...chrome, ...edge] : [...edge, ...chrome]).concat(autres)
}

function trouverNavigateur(config) {
  const trouve = [config.chemin, ...candidatsNavigateur(config.prefere)].filter(Boolean).find(c => fs.existsSync(c))
  if (!trouve) throw new Error('aucun navigateur trouvé (Edge ou Chrome) : indiquez son chemin dans la configuration ("navigateur.chemin")')
  return trouve
}

function compilerSitesInterdits(politique) {
  return (politique.sites_interdits || []).map(source => new RegExp(source, 'i'))
}

/** Raison du refus si l'adresse est interdite, sinon null. */
function verifierAdresse(interdits, adresse) {
  if (typeof adresse !== 'string' || !adresse) return null
  for (const motif of interdits) if (motif.test(adresse)) return `site interdit par la politique de ce PC (${motif})`
  return null
}

const attendre = ms => new Promise(r => setTimeout(r, ms))

/**
 * Crée le relais CDP du PC. `envoyer(message)` envoie au Relais ; `journal(...)` écrit le journal local.
 * Renvoie { traiter(message) -> bool, arreter() }.
 */
function creerNavigateur(config, politique, { envoyer, journal, SocketWeb }) {
  const reglages = { actif: true, prefere: 'edge', delai_lancement_s: 20, ...(config || {}) }
  const interdits = compilerSitesInterdits(politique || {})
  const profil = reglages.profil || path.join(os.homedir(), '.mbiling', 'navigateur')
  const canaux = new Map()
  let processus = null
  let port = null
  let lancement = null

  function portActif() {
    try {
      const premiere = fs.readFileSync(path.join(profil, 'DevToolsActivePort'), 'utf8').split(/\r?\n/)[0]
      const n = Number(premiere)
      return Number.isInteger(n) && n > 0 ? n : null
    } catch {
      return null
    }
  }

  async function repond(p) {
    try {
      const r = await fetch(`http://127.0.0.1:${p}/json/version`, { signal: AbortSignal.timeout(1500) })
      return r.ok
    } catch {
      return false
    }
  }

  /** Lance le navigateur si besoin et renvoie son port de débogage local. */
  async function assurerNavigateur() {
    if (port && (await repond(port))) return port
    if (lancement) return lancement
    lancement = (async () => {
      fs.mkdirSync(profil, { recursive: true })
      fs.rmSync(path.join(profil, 'DevToolsActivePort'), { force: true })
      const executable = trouverNavigateur(reglages)
      const args = [
        '--remote-debugging-port=0', // port libre choisi par le navigateur, écrit dans DevToolsActivePort
        '--remote-debugging-address=127.0.0.1',
        `--user-data-dir=${profil}`, // profil séparé du profil personnel de l'utilisateur
        '--no-first-run',
        '--no-default-browser-check',
        ...(reglages.arguments || []),
        'about:blank'
      ]
      processus = spawn(executable, args, { detached: false, stdio: 'ignore', windowsHide: false })
      processus.on('exit', () => {
        processus = null
        port = null
      })
      journal('NAVIGATEUR', `lancement de ${executable}`)
      const limite = Date.now() + reglages.delai_lancement_s * 1000
      while (Date.now() < limite) {
        const p = portActif()
        if (p && (await repond(p))) {
          port = p
          journal('NAVIGATEUR', `prêt sur 127.0.0.1:${p}`)
          return p
        }
        await attendre(200)
      }
      throw new Error(`le navigateur n'a pas ouvert son port de débogage en ${reglages.delai_lancement_s} s`)
    })()
    try {
      return await lancement
    } finally {
      lancement = null
    }
  }

  async function http(message) {
    try {
      if (!reglages.actif) throw new Error('le navigateur des Mains est désactivé sur ce PC')
      const p = await assurerNavigateur()
      const chemin = String(message.chemin || '/json/version')
      if (!chemin.startsWith('/json')) throw new Error('chemin CDP non autorisé')
      const r = await fetch(`http://127.0.0.1:${p}${chemin}`, { method: message.methode || 'GET' })
      envoyer({ t: 'cdp_http_rep', id: message.id, statut: r.status, corps: await r.text() })
    } catch (e) {
      envoyer({ t: 'cdp_http_rep', id: message.id, statut: 502, corps: JSON.stringify({ erreur: e.message }) })
    }
  }

  async function ouvrir(message) {
    const { canal } = message
    try {
      if (!reglages.actif) throw new Error('le navigateur des Mains est désactivé sur ce PC')
      const p = await assurerNavigateur()
      const chemin = String(message.chemin || '')
      if (!chemin.startsWith('/devtools/')) throw new Error('chemin CDP non autorisé')
      const socket = new SocketWeb(`ws://127.0.0.1:${p}${chemin}`)
      canaux.set(canal, socket)
      socket.onopen = () => envoyer({ t: 'cdp_ouvert', canal })
      socket.onmessage = ev => envoyer({ t: 'cdp', canal, d: typeof ev.data === 'string' ? ev.data : ev.data.toString() })
      socket.onerror = () => {}
      socket.onclose = () => {
        if (canaux.delete(canal)) envoyer({ t: 'cdp_fermer', canal })
      }
    } catch (e) {
      envoyer({ t: 'cdp_erreur', canal, raison: e.message })
    }
  }

  /** Message CDP venu de l'agent : refusé ici si son adresse est interdite. */
  function versNavigateur(message) {
    const socket = canaux.get(message.canal)
    if (!socket) return
    let commande = null
    try {
      commande = JSON.parse(message.d)
    } catch {
      commande = null
    }
    if (commande && METHODES_NAVIGATION.has(commande.method)) {
      const refus = verifierAdresse(interdits, commande.params && commande.params.url)
      if (refus) {
        journal('REFUS', `navigation vers ${commande.params.url} : ${refus}`)
        const reponse = { id: commande.id, error: { code: -32000, message: `[Mains Mbiling] ${refus}` } }
        if (commande.sessionId) reponse.sessionId = commande.sessionId
        envoyer({ t: 'cdp', canal: message.canal, d: JSON.stringify(reponse) })
        return
      }
    }
    if (socket.readyState === 1) socket.send(message.d)
  }

  function fermer(canal) {
    const socket = canaux.get(canal)
    canaux.delete(canal)
    if (socket) {
      try {
        socket.close()
      } catch {}
    }
  }

  return {
    /** Traite un message du Relais destiné au navigateur ; renvoie false s'il ne le concerne pas. */
    traiter(message) {
      if (message.t === 'cdp_http') void http(message)
      else if (message.t === 'cdp_ouvrir') void ouvrir(message)
      else if (message.t === 'cdp') versNavigateur(message)
      else if (message.t === 'cdp_fermer') fermer(message.canal)
      else return false
      return true
    },
    /** La connexion au Relais est coupée : les canaux en cours n'ont plus de destinataire. */
    deconnecte() {
      for (const canal of [...canaux.keys()]) fermer(canal)
    },
    arreter() {
      for (const canal of [...canaux.keys()]) fermer(canal)
      if (processus) {
        try {
          processus.kill()
        } catch {}
      }
    }
  }
}

module.exports = { creerNavigateur, verifierAdresse, compilerSitesInterdits, trouverNavigateur }
