import base64
import subprocess

from openai import OpenAI
from picamera2 import Picamera2

from listen_and_answer import (
    MODEL_NAME,
    MONTHLY_LIMIT_USD,
    NEXT_REQUEST_RESERVE_USD,
    INPUT_PRICE_PER_TOKEN,
    OUTPUT_PRICE_PER_TOKEN,
    BudgetExceeded,
    load_api_key,
    load_owner_name,
    load_usage,
    save_usage,
    set_face,
    speak,
)


# Old USB webcam (icSpring), captured via ffmpeg/v4l2. Replaced by the
# OV5647 CSI camera via picamera2 below.
# CAMERA_DEVICE = (
#     "/dev/v4l/by-id/"
#     "usb-icSpring_icspring_camera_202404260001-video-index0"
# )

IMAGE_PATH = "/tmp/atlas-vision.jpg"


def capture_image():
    # subprocess.run(
    #     [
    #         "ffmpeg",
    #         "-hide_banner",
    #         "-loglevel", "error",
    #         "-y",
    #         "-f", "v4l2",
    #         "-input_format", "mjpeg",
    #         "-video_size", "640x480",
    #         "-i", CAMERA_DEVICE,
    #         "-frames:v", "1",
    #         IMAGE_PATH,
    #     ],
    #     check=True,
    # )

    picam2 = Picamera2()
    config = picam2.create_still_configuration(
        main={"size": (640, 480)}
    )
    picam2.configure(config)
    picam2.start()
    picam2.capture_file(IMAGE_PATH)
    picam2.stop()


def analyze_image():
    usage = load_usage()

    print(
        f"Local API spending for {usage['month']}: "
        f"${usage['spent_usd']:.6f} of ${MONTHLY_LIMIT_USD:.2f}"
    )

    if (
        usage["spent_usd"] + NEXT_REQUEST_RESERVE_USD
        > MONTHLY_LIMIT_USD
    ):
        raise BudgetExceeded(
            "The local monthly API spending limit has been reached."
        )

    with open(IMAGE_PATH, "rb") as image_file:
        encoded_image = base64.b64encode(
            image_file.read()
        ).decode("utf-8")

    client = OpenAI(
        api_key=load_api_key(),
        max_retries=0,
        timeout=30.0,
    )

    response = client.responses.create(
        model=MODEL_NAME,
        reasoning={"effort": "none"},
        instructions=(
            f"You are A.T.L.A.S., {load_owner_name()}'s desk robot. "
            "Describe what the camera sees in one or two natural spoken "
            "sentences. Mention the main objects and anything important "
            "that appears to be happening. Be direct and do not use "
            "markdown or bullet points."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "What do you see through your camera?",
                    },
                    {
                        "type": "input_image",
                        "image_url": (
                            "data:image/jpeg;base64,"
                            + encoded_image
                        ),
                        "detail": "low",
                    },
                ],
            }
        ],
        max_output_tokens=120,
    )

    input_tokens = int(
        getattr(response.usage, "input_tokens", 0) or 0
    )
    output_tokens = int(
        getattr(response.usage, "output_tokens", 0) or 0
    )

    request_cost = (
        input_tokens * INPUT_PRICE_PER_TOKEN
        + output_tokens * OUTPUT_PRICE_PER_TOKEN
    )

    usage["spent_usd"] += request_cost
    usage["requests"] += 1
    save_usage(usage)

    print(
        f"Tokens: {input_tokens} input, "
        f"{output_tokens} output"
    )
    print(f"Estimated vision cost: ${request_cost:.6f}")
    print(
        f"Local monthly total: "
        f"${usage['spent_usd']:.6f}"
    )

    answer = response.output_text.strip()

    if not answer:
        return "I captured an image, but I could not describe it."

    return answer


def main():
    try:
        set_face("thinking")

        print("Capturing camera image...")
        capture_image()

        print("Analyzing image...")
        answer = analyze_image()

        print("A.T.L.A.S.:", answer)
        speak(answer)

    except BudgetExceeded as error:
        print("Budget protection:", error)
        speak("My monthly online vision budget has been reached.")

    except Exception as error:
        print("Vision error:", type(error).__name__, error)
        speak("I ran into an error while checking the camera.")

    finally:
        set_face("happy")


if __name__ == "__main__":
    main()
