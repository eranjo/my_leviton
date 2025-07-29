import logging
import usb.core
import usb.util
from .usb_utils import find_usb_device, reset_usb_device
from .constants import VID, PID, POLL_COMMAND, LIGHTS

_LOGGER = logging.getLogger(__name__)

def poll_light_state(lock, hass):
    """
    Poll the USB device to retrieve the current light states and update Home Assistant entities.

    Args:
        lock (threading.Lock): Thread-safe lock for USB operations.
        hass (HomeAssistant): The Home Assistant instance.

    Returns:
        None
    """
    with lock:
        # Locate the USB device
        device = find_usb_device(VID, PID)
        if not device:
            _LOGGER.error("Cannot poll state. USB device is not connected.")
            return

        _LOGGER.debug("Starting polling for USB responses.")

        try:
            # Send the polling command
            device.write(1, POLL_COMMAND)
            responses = []
            combined_responses = []
            current_combined = []
            last_response = None
            repeated_count = 0

            while True:
                try:
                    response = device.read(0x81, 64, timeout=1000)
                    _LOGGER.debug(f"Raw USB response: {response}")

                    # Check if the current response matches the last response
                    if last_response == response:
                        repeated_count += 1
                        _LOGGER.debug(f"Repeated response count: {repeated_count}")
                        if repeated_count >= 4:
                            _LOGGER.debug("Triple duplicate response detected. Ending polling.")
                            break
                    else:
                        repeated_count = 0  # Reset the count if the response differs

                    last_response = response  # Update the last response for comparison

                    if len(response) >= 2:
                        num_significant_bytes = response[1]
                        significant_data = response[2:2 + num_significant_bytes]
                        cleaned_data = [byte for byte in significant_data if byte != 0xFF]

                        if len(cleaned_data) > 0 and cleaned_data[0] == 0x00:
                            if current_combined:
                                combined_responses.append(current_combined)
                            current_combined = cleaned_data
                        else:
                            current_combined.extend(cleaned_data)

                        responses.append(cleaned_data)

                except usb.core.USBError as e:
                    if e.errno == 75:
                        _LOGGER.warning("USB Overflow error occurred. Skipping this read.")
                        continue
                    else:
                        _LOGGER.error(f"USB error during polling: {e}")
                        break

            if current_combined:
                combined_responses.append(current_combined)

            # Format and parse responses
            formatted_responses = [format_response(response) for response in combined_responses]
            formatted_responses = [resp for resp in formatted_responses if resp is not None]

            update_ha_entities(hass, parse_light_states(formatted_responses))

        except Exception as e:
            _LOGGER.error(f"Failed to poll light state: {e}")
        finally:
            reset_usb_device(device)

import re

def normalize_name_for_ha(name):
    """
    Normalize a light name specifically for Home Assistant entity IDs.

    Args:
        name (str): The original light name.

    Returns:
        str: A normalized name suitable for Home Assistant entity IDs.
    """
    name = name.replace("ä", "a").replace("ö", "o")
    name = name.replace("(", "").replace(")", "")
    return re.sub(r"[^a-zA-Z0-9_]+", "_", name).lower()

def update_ha_entities(hass, states):
    """
    Update the state of Home Assistant entities based on the parsed light states.

    Args:
        hass (HomeAssistant): The Home Assistant instance.
        states (dict): A dictionary of light names and their states (True for ON, False for OFF).

    Returns:
        None
    """
    for light_name, is_on in states.items():
        # Normalize the light name for entity ID
        entity_id = f"light.{normalize_name_for_ha(light_name)}"

        _LOGGER.debug(f"Updating entity {entity_id} to {'on' if is_on else 'off'}.")

        try:
            # Get the current attributes of the entity
            current_state = hass.states.get(entity_id)
            attributes = current_state.attributes if current_state else {}

            # Update the state in Home Assistant without altering attributes
            hass.states.async_set(entity_id, "on" if is_on else "off", attributes)
        except Exception as e:
            _LOGGER.error(f"Failed to update entity id {entity_id}: {e}")



def parse_light_states(formatted_responses):
    """
    Parse the formatted responses and match them to the correct entities dynamically.

    Args:
        formatted_responses (list): List of formatted response data as strings.

    Returns:
        dict: Dictionary of entity names and their on/off states.
    """
    states = {}
    _LOGGER.debug(f"Starting to parse formatted responses: {formatted_responses}")

    for formatted_response in formatted_responses:
        if formatted_response is None:
            _LOGGER.warning("Skipping invalid response.")
            continue

        # Extract the state from the formatted response
        try:
            state = formatted_response.split(" | ")[0].replace("State: ", "")
            response_hex = formatted_response.split(" | ")[1].replace("Hex: ", "")
            _LOGGER.debug(f"Parsed state: {state}, Response Hex: {response_hex}")
        except IndexError:
            _LOGGER.error(f"Invalid formatted response structure: {formatted_response}")
            continue

        # Match the response with the LIGHTS constants
        for light in LIGHTS:
            formatted_on = ", ".join(f"0x{byte:02X}" for byte in light["on"][8:12])
            formatted_off = ", ".join(f"0x{byte:02X}" for byte in light["off"][8:12])
            response_middle = ", ".join(response_hex.split(", ")[1:5])

            _LOGGER.debug(f"Response middle: {response_middle}, On: {formatted_on}, Off: {formatted_off}")

            if response_middle == formatted_on:
                states[light["name"]] = (state == "ON")
            elif response_middle == formatted_off:
                states[light["name"]] = (state == "OFF")

        # Log the state updates after processing each response
        _LOGGER.debug(f"Processed response: {response_hex}, Current States: {states}")

    if not states:
        _LOGGER.warning(f"No matching lights found for responses: {formatted_responses}")
    return states

def format_response(data):
    """
    Process and format the given data into a structured response.

    Args:
        data (list): The list of bytes.

    Returns:
        str: The formatted response string with hex for the first 7 bytes,
             ASCII for the remaining bytes, and entity state information.
    """
   
    # Check for the first occurrence of 0x7E and trim bytes before it
    if 0x7E in data:
        data = data[data.index(0x7E):]
    else:
        return None

    # Validate the 2nd byte
    if len(data) < 2 or data[1] not in {0x26, 0x1E, 0x2E}:
        return None

    # Validate the 6th byte
    if len(data) < 7 or data[6] not in {0x00, 0x64}:
        return None

    # Extract the first 7 bytes for hex formatting
    hex_part = data[:7]
    hex_format = ", ".join(f"0x{byte:02X}" for byte in hex_part)

    # Determine state based on the 7th byte
    state = "OFF" if data[6] == 0x00 else "ON" if data[6] == 0x64 else "UNKNOWN"

    # Extract the remaining bytes for ASCII formatting
    ascii_part = data[7:]
    ascii_raw = "".join(chr(byte) if 32 <= byte <= 126 else '.' for byte in ascii_part)

    # Trim leading and trailing non-alphanumeric characters
    trimmed_ascii = "".join(c for c in ascii_raw if c.isalnum() or c == '.')
    trimmed_ascii = trimmed_ascii.lstrip(". ").rstrip(". ")

    # Limit the ASCII representation to 15 characters
    limited_ascii = trimmed_ascii[:15]

    # Use a counter to generate default names
    if not hasattr(format_response, "dimmer_counter"):
        format_response.dimmer_counter = 1
    if not hasattr(format_response, "relay_counter"):
        format_response.relay_counter = 1
    if not hasattr(format_response, "unknown_counter"):
        format_response.unknown_counter = 1

    # Determine the final entity name
    if not limited_ascii:
        if data[1] in {0x1E, 0x2E}:
            final_ascii = f"Dimmer{format_response.dimmer_counter}"
            format_response.dimmer_counter += 1
        elif data[1] == 0x26:
            final_ascii = f"Relay{format_response.relay_counter}"
            format_response.relay_counter += 1
        else:
            final_ascii = f"Unknown{format_response.unknown_counter}"
            format_response.unknown_counter += 1
    else:
        final_ascii = limited_ascii

    # Combine and return the formatted response
    if ascii_part:
        return f"State: {state} | Hex: {hex_format} | Entity_Name: {final_ascii}"
    else:
        return f"Hex: {hex_format}"
