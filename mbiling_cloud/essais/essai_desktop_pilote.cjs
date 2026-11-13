'use strict'
/**
 * Pilote de Mbiling Desktop pour essai_desktop.py (ne s'utilise pas seul).
 *
 * Lance l'application empaquetée dans un bac à sable (données dans un dossier temporaire,
 * démarrage simulé : aucun moteur local), enregistre le jeton d'appareil par le même pont que
 * la page « Ce PC (Mains) », puis attend la connexion au Relais.
 *
 *   node essai_desktop_pilote.cjs <Mbiling.exe> <dossier de données> <dossier captures>
 *   variable MBILING_ESSAI_JETON : le jeton d'appareil à saisir
 *
 * Écrit « PRET » sur la sortie quand les Mains sont connectées, puis attend « FIN » sur l'entrée.
 * Capture : l'écran du premier lancement (adresse proposée).
 */
const fs = require('node:fs')
const path = require('node:path')
const readline = require('node:readline')
const { createRequire } = require('node:module')

const [exe, donnees, captures] = process.argv.slice(2)
const requireDesktop = createRequire(path.join(__dirname, '..', '..', 'apps', 'desktop', 'package.json'))
const { _electron } = requireDesktop('playwright')

const attendre = ms => new Promise(r => setTimeout(r, ms))

async function principal() {
  fs.mkdirSync(captures, { recursive: true })
  const env = {}
  for (const [cle, valeur] of Object.entries(process.env)) {
    if (valeur !== undefined && !/^(MBILING_|HERMES_)/.test(cle)) env[cle] = valeur
  }
  Object.assign(env, {
    MBILING_HOME: path.join(donnees, 'mbiling-home'),
    MBILING_DESKTOP_USER_DATA_DIR: donnees,
    MBILING_DESKTOP_IGNORE_EXISTING: '1',
    MBILING_DESKTOP_ISOLATED_BACKEND: '1',
    MBILING_DESKTOP_APP_NAME: 'MbilingEssai',
    MBILING_DESKTOP_SKIP_QUIT_CONFIRM: '1',
    MBILING_DESKTOP_SKIP_PROTOCOL_REGISTRATION: '1',
    MBILING_DESKTOP_BOOT_FAKE: '1',
    MBILING_DESKTOP_BOOT_FAKE_STEP_MS: '400'
  })

  const app = await _electron.launch({ executablePath: exe, args: ['--disable-gpu', '--no-sandbox'], env })
  const page = await app.firstWindow()
  await page.waitForFunction(() => Boolean(window.mbilingDesktop?.mains), null, { timeout: 60000 })

  // Écran du premier lancement : un seul bouton « Se connecter », aucun champ d'adresse ni de jeton.
  await attendre(6000)
  await page.screenshot({ path: path.join(captures, '1-premier-lancement.png') })
  const premierLancement = await page.evaluate(() => ({
    boutonConnexion: Array.from(document.querySelectorAll('button')).some(b => /Sign in to Mbiling|Se connecter à Mbiling/.test(b.textContent || '')),
    champs: Array.from(document.querySelectorAll('input')).filter(i => i.type !== 'checkbox' && i.type !== 'hidden').length,
    adresseParDefaut: null
  }))
  premierLancement.adresseParDefaut = (await page.evaluate(() => window.mbilingDesktop.getDefaultConnection())).adresse
  console.log('PREMIER_LANCEMENT ' + JSON.stringify(premierLancement))

  const avant = await page.evaluate(() => window.mbilingDesktop.mains.status())
  console.log('ETAT_AVANT ' + JSON.stringify(avant))
  await page.evaluate(jeton => window.mbilingDesktop.mains.setToken(jeton), process.env.MBILING_ESSAI_JETON)

  let etat = null
  for (let i = 0; i < 60; i++) {
    etat = await page.evaluate(() => window.mbilingDesktop.mains.status())
    if (etat.connecte) break
    await attendre(500)
  }
  console.log('ETAT ' + JSON.stringify(etat))
  console.log(etat && etat.connecte ? 'PRET' : 'ECHEC_CONNEXION')

  const lignes = readline.createInterface({ input: process.stdin })
  for await (const ligne of lignes) {
    if (ligne.trim() === 'FIN') break
  }
  await app.close().catch(() => undefined)
  console.log('FERME')
}

principal().catch(erreur => {
  console.error('ERREUR', erreur)
  process.exit(1)
})
