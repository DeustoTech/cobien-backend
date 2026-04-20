# cobien-backend/apps/eventos/call_monitor.py
"""
Système de surveillance des appels Twilio.

Détecte automatiquement quand une room se termine et si l'appel a été manqué.
Fonctionne sans webhooks Twilio, juste avec l'API Twilio.
"""

import threading
import time
import json
import os
from datetime import datetime
from twilio.rest import Client
from twilio.base.exceptions import TwilioException
from django.conf import settings

from apps.pizarra.device_queue import enqueue_notification


class CallMonitor:
    """
    Moniteur d'appels en cours.
    
    Vérifie périodiquement l'état des rooms Twilio et détecte les appels manqués.
    """
    
    def __init__(self):
        # Tracker des appels actifs
        # Structure: {
        #     "maria": {
        #         "caller": "Ana",
        #         "room": "maria",
        #         "start_time": "2024-12-19T15:30:00",
        #         "answered": False
        #     }
        # }
        self.active_calls = {}
        
        # Client Twilio
        self.twilio_client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )
        
        # Thread de surveillance
        self.monitor_thread = None
        self.running = False
        self.enabled = os.getenv("COBIEN_ENABLE_CALL_MONITOR", "1").strip().lower() not in {"0", "false", "no", "off"}
        
        print("[CALL MONITOR] 🎯 Initialisé")
    
    def start(self):
        """Démarre la surveillance en arrière-plan"""
        if not self.enabled:
            print("[CALL MONITOR] ⏸️ Désactivé par environnement")
            return

        if self.running:
            print("[CALL MONITOR] ⚠️ Déjà démarré")
            return
        
        self.running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        print("[CALL MONITOR] ✅ Surveillance démarrée")
    
    def stop(self):
        """Arrête la surveillance"""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        
        print("[CALL MONITOR] 🛑 Surveillance arrêtée")
    
    def add_call(self, room_name: str, caller: str):
        """
        Ajoute un appel au tracker.
        
        Args:
            room_name: Nom de la room Twilio (ex: "maria")
            caller: Nom de l'appelant (ex: "Ana")
        """
        if not self.enabled:
            return

        self.active_calls[room_name] = {
            "caller": caller,
            "room": room_name,
            "start_time": datetime.now().isoformat(),
            "answered": False,
            "grace_period_end": datetime.now().timestamp() + 10 # attendre 10 seconde avant de vérifier si quelqu'un est partie de l'appel
        }
        
        print(f"[CALL MONITOR] 📞 Nouvel appel tracké")
        print(f"[CALL MONITOR]    Room: {room_name}")
        print(f"[CALL MONITOR]    Caller: {caller}")
        print(f"[CALL MONITOR]    Total actifs: {len(self.active_calls)}")
    
    def mark_answered(self, room_name: str):
        """
        Marque un appel comme répondu.
        
        Args:
            room_name: Nom de la room Twilio
        """
        if not self.enabled:
            return

        if room_name in self.active_calls:
            self.active_calls[room_name]["answered"] = True
            print(f"[CALL MONITOR] ✅ Appel répondu: {room_name}")
        else:
            print(f"[CALL MONITOR] ⚠️ Appel inconnu: {room_name}")
    
    def _monitor_loop(self):
        """
        Boucle de surveillance principale.
        
        Vérifie toutes les 10 secondes l'état des rooms actives.
        """
        print("[CALL MONITOR] 🔄 Boucle de surveillance démarrée")
        
        while self.running:
            try:
                self._check_active_calls()
            except Exception as e:
                print(f"[CALL MONITOR] ❌ Erreur dans la boucle: {e}")
                import traceback
                traceback.print_exc()
            
            # Attendre 10 secondes avant la prochaine vérification
            time.sleep(10)
        
        print("[CALL MONITOR] 🔄 Boucle de surveillance terminée")
    
    def _check_active_calls(self):
        """Vérifie l'état de tous les appels actifs"""
        if not self.active_calls:
            return
        
        print(f"[CALL MONITOR] 🔍 Vérification de {len(self.active_calls)} appel(s) actif(s)")
        
        # Copier la liste pour éviter modification pendant itération
        rooms_to_check = list(self.active_calls.keys())
        
        for room_name in rooms_to_check:
            self._check_room(room_name)
    
    def _check_room(self, room_name: str):
        """
        Vérifie l'état d'une room spécifique.
        
        Args:
            room_name: Nom de la room à vérifier
        """
        call_info = self.active_calls.get(room_name)
        if not call_info:
            return
        
        #Ne pas vérifier les 10 premières secondes
        now = time.time()
        grace_end = call_info.get("grace_period_end", 0)
        
        if now < grace_end:
            remaining = int(grace_end - now)
            print(f"[CALL MONITOR] ⏳ Room {room_name} en période de grâce ({remaining}s restantes)")
            return
        
        try:
            # ✅ Vérifier si la room existe encore sur Twilio
            rooms = self.twilio_client.video.rooms.list(
                unique_name=room_name,
                status='in-progress',
                limit=1
            )
            
            if rooms:
                # ✅ Room encore active
                print(f"[CALL MONITOR] ⏳ Room active: {room_name}")
                return
            
            # ❌ Room terminée
            print(f"[CALL MONITOR] 🏁 Room terminée: {room_name}")
            
            if not call_info["answered"]:
                # ❌ Appel manqué !
                print(f"[CALL MONITOR] 📵 Appel manqué détecté !")
                print(f"[CALL MONITOR]    Room: {room_name}")
                print(f"[CALL MONITOR]    Caller: {call_info['caller']}")
                
                self._send_missed_call_notification(
                    room_name=room_name,
                    caller=call_info["caller"],
                    timestamp=call_info["start_time"]
                )
            else:
                # ✅ Appel normal terminé
                print(f"[CALL MONITOR] ✅ Appel normal terminé: {room_name}")
            
            # Retirer du tracker
            del self.active_calls[room_name]
            print(f"[CALL MONITOR] 🧹 Room retirée du tracker: {room_name}")
        
        except TwilioException as e:
            print(f"[CALL MONITOR] ❌ Erreur Twilio room {room_name}: {e}")
            if getattr(e, "status", None) == 401 or "HTTP 401" in str(e):
                print("[CALL MONITOR] 🛑 Désactivation du monitor suite à une erreur d'authentification Twilio")
                self.enabled = False
                self.running = False
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"[CALL MONITOR] ❌ Erreur vérification room {room_name}: {e}")
            import traceback
            traceback.print_exc()
    
    def _send_missed_call_notification(self, room_name: str, caller: str, timestamp: str):
        """
        Envoie une notification de file d'attente pour appel manqué.
        
        Args:
            room_name: Nom de la room (ex: "maria")
            caller: Nom de l'appelant (ex: "Ana")
            timestamp: Timestamp de début d'appel
        """
        try:
            payload = {
                "type": "missed_call",
                "from": caller,
                "to": room_name,
                "room": room_name,
                "timestamp": timestamp
            }
            enqueue_notification(room_name, payload)
            print(f"[CALL MONITOR] ✓ Notification queue envoyée")
            print(f"[CALL MONITOR]   Type: missed_call")
            print(f"[CALL MONITOR]   From: {caller}")
            print(f"[CALL MONITOR]   To: {room_name}")
            print(f"[CALL MONITOR]   Payload: {json.dumps(payload)}")
        
        except Exception as e:
            print(f"[CALL MONITOR] ✗ Erreur queue: {e}")
            import traceback
            traceback.print_exc()


# ========== INSTANCE GLOBALE ==========
# Créée au démarrage de Django
call_monitor = CallMonitor()
