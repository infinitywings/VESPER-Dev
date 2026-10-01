idf_component_register(
    SRCS
        "main.c"
        "device_control.c"
        "wifi_manager.c"
        "mqtt_handler.c"
        "sensor_driver.c"
    INCLUDE_DIRS "."
    EMBED_FILES
        "device_info.json"
        "onboarding_config.json"
)

# Link SmartThings SDK
target_link_libraries(${COMPONENT_LIB} PUBLIC iotcore)
