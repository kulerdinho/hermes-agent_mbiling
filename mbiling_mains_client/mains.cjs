'use strict'
/**
 * Mains Mbiling — côté PC.
 *
 * Se connecte en sortie au Relais, reçoit les commandes de l'agent et les exécute avec bash,
 * APRÈS avoir appliqué la politique locale. La politique locale est le dernier verrou :
 * elle peut refuser ce que le Cloud a accepté, jamais l'inverse.
 *
 * Aucune valeur n'est écrite ici : tout vient du fichier de configuration (voir
 * config.exemple.json). Le jeton d'appareil est lu dans la variable d'environnement
 * que la configuration désigne.
 *
 * Utilisation autonome :  node mains.cjs chemin/vers/config.json
 * Dans Mbiling Desktop :  require('./mains.cjs').demarrer(config, { journal, jeton, bashEmbarques })
 *   jeton          : le jeton d'appareil (au lieu de la variable d'environnement) ;
 *   bashEmbarques  : chemins de bash.exe à essayer en premier (Git portable livré avec l'application) ;
 *   surEtat        : appelé à chaque changement d'état ({ connecte, appareil, raison }).
 */
const { spawn, spawnSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const { creerNavigateur, trouverNavigateur } = require('./navigateur.cjs')

function trouverBash(config, autres = []) {
  const candidats = [
    config.bash,
    ...autres,
    process.env.MBILING_BASH,
    'C:\\Program Files\\Git\\bin\\bash.exe',
    'C:\\Program Files (x86)\\Git\\bin\\bash.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Programs', 'Git', 'bin', 'bash.exe'),
    '/bin/bash',
    '/usr/bin/bash'
  ].filter(Boolean)
  const trouve = candidats.find((c) => fs.existsSync(c))
  if (!trouve) throw new Error('bash introuvable : indiquez son chemin dans la configuration ("bash")')
  return trouve
}

/** C:\Users\x\Documents -> /c/Users/x/Documents (forme attendue par Git Bash). */
function versCheminBash(chemin) {
  const m = /^([A-Za-z]):[\\/](.*)$/.exec(chemin)
  if (!m) return chemin.replace(/\\/g, '/')
  return `/${m[1].toLowerCase()}/${m[2].replace(/\\/g, '/')}`
}

function compilerPolitique(politique) {
  return {
    interdits: (politique.interdits || []).map((source) => new RegExp(source, 'i')),
    dossiers: (politique.dossiers_autorises || []).map((d) => path.resolve(d))
  }
}

/** Renvoie la raison du refus, ou null si le script est acceptable. */
function verifierPolitique(politique, script) {
  for (const motif of politique.interdits) {
    if (motif.test(script)) return `la politique locale interdit ce qui correspond à ${motif}`
  }
  return null
}

/**
 * Arrête une commande et tous ses descendants.
 * Sous Windows, taskkill ne voit pas les processus lancés par Git Bash : on demande donc
 * à bash lui-même de tuer le groupe de processus, dont l'identifiant est dans fichierPid.
 */
function tuerArbre(tache) {
  if (!tache || tache.enfant.exitCode !== null) return
  const { enfant, bash, fichierPid } = tache
  if (process.platform === 'win32') {
    spawnSync(bash, ['-c', 'p=$(cat "$0" 2>/dev/null) && kill -9 -- "-$p" 2>/dev/null; kill -9 "$p" 2>/dev/null', versCheminBash(fichierPid)], { windowsHide: true })
    spawnSync('taskkill', ['/PID', String(enfant.pid), '/T', '/F'], { windowsHide: true })
  } else {
    try {
      process.kill(-enfant.pid, 'SIGKILL')
    } catch {
      enfant.kill('SIGKILL')
    }
  }
}

function demarrer(config, options = {}) {
  const journal = options.journal || ((...a) => console.log(new Date().toISOString(), ...a))
  const SocketWeb = options.WebSocket || globalThis.WebSocket || require('ws')
  const jeton = options.jeton || process.env[config.jeton_env]
  if (!jeton) throw new Error(`variable ${config.jeton_env} absente : jeton d'appareil requis`)
  const bash = trouverBash(config, options.bashEmbarques || [])
  const surEtat = options.surEtat || (() => {})
  const politique = compilerPolitique(config.politique || {})
  for (const dossier of politique.dossiers) fs.mkdirSync(dossier, { recursive: true })
  const enCours = new Map()
  const dossierTravail = path.join(os.tmpdir(), 'mbiling-mains')
  fs.mkdirSync(dossierTravail, { recursive: true })
  let socket = null
  let arrete = false
  let attente = config.reconnexion.delai_initial_ms

  const envoyer = (message) => {
    if (socket && socket.readyState === 1) socket.send(JSON.stringify(message))
  }

  // Lot 6 : le navigateur du PC, piloté par l'agent à travers le Relais (tunnel CDP).
  const navigateur = creerNavigateur(config.navigateur, config.politique || {}, { envoyer, journal, SocketWeb })

  function executer(demande) {
    const refus = verifierPolitique(politique, demande.script)
    if (refus) {
      journal('REFUS', demande.id, refus)
      envoyer({ t: 'sortie', id: demande.id, d: `[Mains Mbiling] Commande refusée sur cet ordinateur : ${refus}.\n` })
      // 77 (EX_NOPERM) : 126 ferait croire au moteur qu'un fichier n'est pas exécutable (« chmod +x »).
      envoyer({ t: 'fin', id: demande.id, code: 77 })
      return
    }
    // Le script passe par un fichier : la ligne de commande de Windows est limitée à 32 Ko.
    const fichierScript = path.join(dossierTravail, `${demande.id}.sh`)
    const fichierPid = path.join(dossierTravail, `${demande.id}.pid`)
    fs.writeFileSync(fichierScript, demande.script, { encoding: 'utf8', mode: 0o600 })
    const lanceur = 'echo $$ > "$0"; if [ "$1" = login ]; then exec bash -l "$2"; else exec bash "$2"; fi'
    const args = ['-c', lanceur, versCheminBash(fichierPid), demande.login ? 'login' : 'simple', versCheminBash(fichierScript)]
    const enfant = spawn(bash, args, {
      cwd: politique.dossiers[0] || os.homedir(),
      env: { ...process.env, ...(config.jeton_env ? { [config.jeton_env]: '' } : {}) },
      windowsHide: true,
      detached: process.platform !== 'win32',
      stdio: ['pipe', 'pipe', 'pipe']
    })
    const tache = { enfant, bash, fichierPid }
    enCours.set(demande.id, tache)
    journal('EXEC', demande.id, `${demande.script.length} caractères`)
    const sortie = (bloc) => envoyer({ t: 'sortie', id: demande.id, d: bloc.toString('utf8') })
    enfant.stdout.on('data', sortie)
    enfant.stderr.on('data', sortie)
    enfant.stdin.on('error', () => {})
    enfant.stdin.end(demande.stdin == null ? '' : demande.stdin)
    const minuteur = setTimeout(() => tuerArbre(tache), (demande.timeout + config.marge_timeout_s) * 1000)
    let termine = false
    const finir = (code) => {
      if (termine) return
      termine = true
      clearTimeout(minuteur)
      enCours.delete(demande.id)
      for (const f of [fichierScript, fichierPid]) fs.rm(f, { force: true }, () => {})
      journal('FIN', demande.id, code)
      envoyer({ t: 'fin', id: demande.id, code })
    }
    enfant.on('error', (e) => {
      sortie(Buffer.from(`[Mains Mbiling] impossible de lancer bash : ${e.message}\n`))
      finir(127)
    })
    enfant.on('close', (code, signal) => finir(code === null ? (signal ? 137 : 1) : code))
  }

  function connecter() {
    if (arrete) return
    socket = new SocketWeb(config.relais_url)
    socket.onopen = () => {
      envoyer({
        t: 'bonjour',
        jeton,
        info: {
          nom: os.hostname(),
          systeme: `${os.platform()} ${os.release()}`,
          tmp: '/tmp',
          dossier_depart: politique.dossiers[0] ? versCheminBash(politique.dossiers[0]) : null
        }
      })
    }
    socket.onmessage = (evenement) => {
      let message
      try {
        message = JSON.parse(typeof evenement.data === 'string' ? evenement.data : evenement.data.toString())
      } catch {
        return
      }
      if (message.t === 'bienvenue') {
        attente = config.reconnexion.delai_initial_ms
        journal('CONNECTÉ', message.appareil)
        surEtat({ connecte: true, appareil: message.appareil || null, raison: null })
        if (options.surConnexion) options.surConnexion(message)
      } else if (message.t === 'refus') {
        journal('REFUSÉ PAR LE RELAIS', message.raison)
        surEtat({ connecte: false, appareil: null, raison: `refusé par le Relais : ${message.raison}` })
        arrete = true
      } else if (message.t === 'exec') {
        executer(message)
      } else if (message.t === 'annuler') {
        journal('ANNULER', message.id)
        tuerArbre(enCours.get(message.id))
      } else {
        navigateur.traiter(message)
      }
    }
    socket.onerror = () => {}
    socket.onclose = () => {
      for (const tache of enCours.values()) tuerArbre(tache)
      navigateur.deconnecte()
      if (arrete) return
      surEtat({ connecte: false, appareil: null, raison: 'déconnecté' })
      journal('DÉCONNECTÉ', `nouvel essai dans ${attente} ms`)
      setTimeout(connecter, attente)
      attente = Math.min(attente * 2, config.reconnexion.delai_max_ms)
    }
  }

  connecter()
  return {
    arreter() {
      arrete = true
      for (const tache of enCours.values()) tuerArbre(tache)
      navigateur.arreter()
      if (socket) socket.close()
    }
  }
}

module.exports = { demarrer, verifierPolitique, compilerPolitique, versCheminBash, trouverBash, trouverNavigateur }

if (require.main === module) {
  const chemin = process.argv[2]
  if (!chemin) {
    console.error('Usage : node mains.cjs chemin/vers/config.json')
    process.exit(2)
  }
  demarrer(JSON.parse(fs.readFileSync(chemin, 'utf8')))
}
