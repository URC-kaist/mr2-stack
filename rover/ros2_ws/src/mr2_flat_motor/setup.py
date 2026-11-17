from setuptools import setup

setup(
    name='mr2_flat_motor', version='0.1.0', packages=['mr2_flat_motor'],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/mr2_flat_motor']),
        ('share/mr2_flat_motor', ['package.xml']),
    ],
    install_requires=['setuptools'], zip_safe=True,
    maintainer='mr2', maintainer_email='gmmyung@kaist.ac.kr',
    description='Isolated flat-rover CAN motor trigger control', license='MIT',
    entry_points={'console_scripts': ['flat_motor = mr2_flat_motor.node:main']},
)
