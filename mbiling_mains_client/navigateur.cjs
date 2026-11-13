'use strict'
/**
 * Navigateur des Mains — côté PC (phase 0, lot 6).
 *
 * L'agent pilote un navigateur SUR CE PC, par le protocole CDP, à travers le Relais :
 *   - on lance le navigateur déjà installé (Edge ou Chrome), avec un profil dédié à Mbiling
 *     (séparé du profil personnel), en fenêtre visible : l'utilisateur voit ce que fait l'agent ;
 *   - son port de débogage n'écoute qu'en local (127.0.0.1) ; seul ce module s'y connecte ;
 *   - la politique locale refuse la navigation vers les sites interdits, quoi que dise le serveur.
 *
 * Messages avec le Relais (sur la connexion des Mains) :
 *   Relais -> PC : {"t": "cdp_http", "id", "chemin": "/json/version"}
 *   PC -> Relais : {"t": "cdp_http_reponse", "id", "statut", "corps", "port"} | {"t": "cdp_erreur", "id", "raison"}
 *   Relais -> PC : {"t": "cdp_ouvrir", "id", "chemin": "/devtools/browser/..."}
 *   PC -> Relais : {"t": "cdp_ouvert", "id"}
 *   dans les deux sens : {"t": "cdp", "id", "d": "<trame CDP>"}
 *   Relais -> PC : {"t": "cdp_fermer", "id"}      PC -> Relais : {"t": "cdp_ferme", "id"}
 *
 * Aucune valeur n'est écrite ici : tout vient de la section "navigateur" de la configuration
 * (voir config.exemple.json) et des "sites_interdits" de la politique.
 */
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

// Emplacements habituels des navigateurs (comme pour bash, on cherche ce qui est déjà installé).
const EMPLACEMENTS = {
  edge: [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
    '/usr/bin/microsoft-edge'
  ],
  chrome: [
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Google', 'Chrome', 'Application', 'chrome.exe'),
    '/usr/bin/google-chrome',
    '/usr/bin/chromium'
  ]
}

// Commandes CDP qui ouvrent une adresse : c'est là que la politique des sites s'applique.
const METHODES_NAVIGATION = new Set(['Page.navigate', 'Target.createTarget'])

function trouverNavigateur(reglages) {
  const candidats = [reglages.chemin, ...(reglages.preference || []).flatMap((nom) => EMPLACEMENTS[nom] || [])].filter(Boolean)
  const trouve = candidats.find((c) => fs.existsSync(c))
  if (!trouve) throw new Error('aucun navigateur trouvé : indiquez son chemin dans la configuration ("navigateur.chemin")')
  return trouve
}

/** Renvoie la raison du refus si la trame CDP ouvre un site interdit, sinon null. */
function verifierSite(sitesInterdits, trame) {
  if (!sitesInterdits.length || !trame.includes('"url"')) return null
  let message
  try {
    message = JSON.parse(trame)
  } catch {
    return null
  }
  const adresse = METHODES_NAVIGATION.has(message.method) && message.params ? String(message.params.url || '') : ''
  const motif = adresse && sitesInterdits.find((m) => m.test(adresse))
  return motif ? `la politique locale interdit ce site (${motif})` : null
}

const attendre = (ms) => new Promise((resoudre) => setTimeout(resoudre, ms))

function creerNavigateur(reglages, { journal, envoyer, SocketWeb, sitesInterdits = [] }) {
  const profil = path.resolve(reglages.profil)
  const fichierPort = path.join(profil, 'DevToolsActivePort')
  const interdits = sitesInterdits.map((source) => new RegExp(source, 'i'))
  const canaux = new Map() // id -> { socket, attente: [] }
  let port = null
  let lancement = null

  async function repond(p) {
    try {
      const reponse = await fetch(`http://127.0.0.1:${p}/json/version`, { signal: AbortSignal.timeout(2000) })
      return reponse.ok
    } catch {
      return false
    }
  }

  function lirePort() {
    try {
      const valeur = Number(fs.readFileSync(fichierPort, 'utf8').split('\n')[0])
      return Number.isInteger(valeur) && valeur > 0 ? valeur : null
    } catch {
      return null
    }
  }

  async function lancer() {
    // Un navigateur de ce profil tourne peut-être encore (lancement précédent) : on le reprend.
    const existant = lirePort()
    if (existant && (await repond(existant))) return existant
    fs.rmSync(fichierPort, { force: true })
    fs.mkdirSync(profil, { recursive: true })
    const executable = trouverNavigateur(reglages)
    // Port 0 : le navigateur choisit un port libre et l'écrit dans DevToolsActivePort.
    // Le débogage à distance n'écoute que sur 127.0.0.1.
    const enfant = spawn(executable, [
      `--user-data-dir=${profil}`,
      '--remote-debugging-port=0',
      '--no-first-run',
      '--no-default-browser-check',
      'about:blank'
    ], { detached: true, stdio: 'ignore' })
    enfant.on('error', (e) => journal('NAVIGATEUR ERREUR', e.message))
    enfant.unref()
    journal('NAVIGATEUR', executable)
    const limite = Date.now() + reglages.delai_demarrage_s * 1000
    while (Date.now() < limite) {
      const p = lirePort()
      if (p && (await repond(p))) return p
      await attendre(200)
    }
    throw new Error(`le navigateur n'a pas ouvert son port de débogage en ${reglages.delai_demarrage_s} s`)
  }

  async function assurer() {
    if (port && (await repond(port))) return port
    if (!lancement) {
      lancement = lancer().finally(() => {
        lancement = null
      })
    }
    port = await lancement
    return port
  }

  async function http(message) {
    try {
      if (!String(message.chemin).startsWith('/json')) throw new Error('chemin CDP non autorisé')
      const p = await assurer()
      const reponse = await fetch(`http://127.0.0.1:${p}${message.chemin}`, { signal: AbortSignal.timeout(10000) })
      envoyer({ t: 'cdp_http_reponse', id: message.id, statut: reponse.status, corps: await reponse.text(), port: p })
    } catch (e) {
      envoyer({ t: 'cdp_erreur', id: message.id, raison: e.message })
    }
  }

  async function ouvrir(message) {
    const canal = { socket: null, attente: [] }
    canaux.set(message.id, canal)
    try {
      if (!String(message.chemin).startsWith('/devtools/')) throw new Error('chemin CDP non autorisé')
      const p = await assurer()
      if (!canaux.has(message.id)) return // fermé pendant le lancement du navigateur
      const socket = new SocketWeb(`ws://127.0.0.1:${p}${message.chemin}`)
      canal.socket = socket
      socket.onopen = () => {
        for (const trame of canal.attente) socket.send(trame)
        canal.attente = []
        envoyer({ t: 'cdp_ouvert', id: message.id })
      }
      socket.onmessage = (evenement) => {
        envoyer({ t: 'cdp', id: message.id, d: typeof evenement.data === 'string' ? evenement.data : evenement.data.toString() })
      }
      socket.onerror = () => {}
      socket.onclose = () => {
        if (canaux.delete(message.id)) envoyer({ t: 'cdp_ferme', id: message.id })
      }
    } catch (e) {
      canaux.delete(message.id)
      envoyer({ t: 'cdp_erreur', id: message.id, raison: e.message })
    }
  }

  function transmettre(message) {
    const canal = canaux.get(message.id)
    if (!canal) return
    const refus = verifierSite(interdits, message.d)
    if (refus) {
      journal('REFUS', message.id, refus)
      // Réponse CDP d'erreur à la commande, sans rien transmettre au navigateur.
      const commande = JSON.parse(message.d)
      envoyer({ t: 'cdp', id: message.id, d: JSON.stringify({ id: commande.id, sessionId: commande.sessionId, error: { code: -32000, message: `[Mains Mbiling] ${refus}` } }) })
      return
    }
    if (canal.socket && canal.socket.readyState === 1) canal.socket.send(message.d)
    else canal.attente.push(message.d)
  }

  function fermer(id) {
    const canal = canaux.get(id)
    canaux.delete(id)
    if (canal && canal.socket) canal.socket.close()
  }

  const traitements = {
    cdp_http: http,
    cdp_ouvrir: ouvrir,
    cdp: transmettre,
    cdp_fermer: (message) => fermer(message.id)
  }

  return {
    /** Traite un message "cdp*" du Relais. */
    traiter(message) {
      const traitement = traitements[message.t]
      if (traitement) traitement(message)
    },
    /** Ferme les canaux CDP (connexion au Relais perdue). Le navigateur reste ouvert. */
    fermerTout() {
      for (const id of [...canaux.keys()]) fermer(id)
    }
  }
}

module.exports = { creerNavigateur, verifierSite, trouverNavigateur }
