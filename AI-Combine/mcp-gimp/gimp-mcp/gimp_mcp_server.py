#!/usr/bin/env python3
# GIMP MCP Server Plugin
# Starts a socket server to allow external control of GIMP via MCP.

import gi
gi.require_version('Gimp', '3.0')
from gi.repository import Gimp
gi.require_version('GimpUi', '3.0')
from gi.repository import GimpUi
from gi.repository import GLib
from gi.repository import Gio
import socket
import threading
import json
import sys
import traceback

def N_(message): return message
def _(message): return GLib.dgettext(None, message)

class MCPPlugin(Gimp.PlugIn):
    def do_query_procedures(self):
        """Register the plugin procedure."""
        return ["plug-in-mcp-server"]

    def do_create_procedure(self, name):
        """Define the procedure properties."""
        procedure = Gimp.ImageProcedure.new(self, name, Gimp.PDBProcType.PLUGIN, self.run, None)
        procedure.set_menu_label(_("Start MCP Server"))
        procedure.set_documentation(_("Starts an MCP server to control GIMP externally"),
                                    _("Starts an MCP server to control GIMP externally"),
                                    name)
        procedure.set_attribution("Your Name", "Your Name", "2023")
        procedure.add_menu_path('<Image>/Filters/Development/')
        return procedure

    def run(self, procedure, run_mode, image, drawables, config, run_data):
        """Run the plugin and start the server."""
        server_thread = threading.Thread(target=self.start_server, daemon=True)
        server_thread.start()
        print("MCP Server started on localhost:9876")
        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def start_server(self):
        """Start a socket server to listen for commands."""
        host = 'localhost'
        port = 9876
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(1)
        print(f"Server listening on {host}:{port}")

        while True:
            try:
                client, addr = sock.accept()
                print(f"Connected to {addr}")
                threading.Thread(target=self.handle_client, args=(client,), daemon=True).start()
            except Exception as e:
                print(f"Server error: {e}")
                break
        sock.close()

    def handle_client(self, client):
        """Handle incoming client connections."""
        buffer = b''
        while True:
            try:
                data = client.recv(1024)
                if not data:
                    break
                buffer += data
                try:
                    command = json.loads(buffer.decode('utf-8'))
                    buffer = b''
                    GLib.idle_add(self.execute_command, command, client)
                except json.JSONDecodeError:
                    continue
            except Exception as e:
                print(f"Client error: {e}")
                break
        client.close()

    def execute_command(self, command, client):
        """Execute commands in GIMP’s main thread."""
        cmd_type = command.get("type")
        params = command.get("params", {})
        response = {}

        try:
            if cmd_type == "call_api":
                api_path = params["api_path"]
                args = params.get("args", [])
                kwargs = params.get("kwargs", {})

                # Dynamically resolve the API method
                module = Gimp
                for part in api_path.split("."):
                    module = getattr(module, part)

                # Call the method
                result = module(*args, **kwargs)

                # Serialize result
                if hasattr(result, "ID"):  # For Gimp objects
                    result = {"id": result.ID}
                elif isinstance(result, list):
                    result = [self.serialize_gimp_object(item) for item in result]
                else:
                    result = str(result)

                response = {"status": "success", "result": result}

            else:
                response = {"status": "error", "message": f"Unknown command: {cmd_type}"}

        except Exception as e:
            response = {"status": "error", "message": str(e), "traceback": traceback.format_exc()}

        client.sendall(json.dumps(response).encode('utf-8'))
        return False  # Remove idle_add callback

    def serialize_gimp_object(self, obj):
        """Serialize Gimp objects to JSON-friendly formats."""
        if hasattr(obj, "ID"):
            return {"id": obj.ID, "type": obj.__class__.__name__}
        return str(obj)

Gimp.main(MCPPlugin.__gtype__, sys.argv)